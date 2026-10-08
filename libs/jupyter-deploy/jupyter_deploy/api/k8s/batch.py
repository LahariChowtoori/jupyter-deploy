from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from kubernetes.client import ApiClient, BatchV1Api, V1Job, V1ObjectMeta


@dataclass(frozen=True)
class CronJobStatus:
    name: str
    schedule: str
    last_schedule_time: str
    active_count: int
    suspended: bool


@dataclass(frozen=True)
class CronJobInfo:
    name: str
    schedule: str
    last_schedule_time: str
    last_successful_time: str
    active_count: int
    suspended: bool
    resource: dict[str, Any] = field(default_factory=dict)


def _format_time(dt: object | None) -> str:
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def get_cronjob_status(batch_api: BatchV1Api, name: str, namespace: str) -> CronJobStatus:
    """Read and parse the cronJob resource, return a Status object."""
    cj = batch_api.read_namespaced_cron_job(name=name, namespace=namespace)
    cj_name = (cj.metadata.name or "") if cj.metadata else ""
    spec = cj.spec
    status = cj.status
    schedule = spec.schedule if spec else ""
    suspended = spec.suspend if spec else False
    last_schedule = _format_time(status.last_schedule_time if status else None)
    active = len(status.active) if status and status.active else 0
    return CronJobStatus(
        name=cj_name,
        schedule=schedule,
        last_schedule_time=last_schedule,
        active_count=active,
        suspended=suspended or False,
    )


def get_cronjob(batch_api: BatchV1Api, name: str, namespace: str) -> CronJobInfo:
    """Read and parse the cronJob resource, return a full serialized object."""
    cj = batch_api.read_namespaced_cron_job(name=name, namespace=namespace)
    cj_name = (cj.metadata.name or "") if cj.metadata else ""
    spec = cj.spec
    status = cj.status
    schedule = spec.schedule if spec else ""
    suspended = spec.suspend if spec else False
    last_schedule = _format_time(status.last_schedule_time if status else None)
    last_success = _format_time(status.last_successful_time if status else None)
    active = len(status.active) if status and status.active else 0
    resource: dict[str, Any] = ApiClient().sanitize_for_serialization(cj)
    return CronJobInfo(
        name=cj_name,
        schedule=schedule,
        last_schedule_time=last_schedule,
        last_successful_time=last_success,
        active_count=active,
        suspended=suspended or False,
        resource=resource,
    )


@dataclass(frozen=True)
class JobResultInfo:
    name: str
    status: str
    completion_time: str


def find_jobs(batch_api: BatchV1Api, namespace: str, label_selector: str) -> list:
    """List Kubernetes Jobs in the namespace using a label selector."""
    jobs = batch_api.list_namespaced_job(namespace=namespace, label_selector=label_selector)
    return list(jobs.items)


def get_last_job_result(batch_api: BatchV1Api, namespace: str, label_selector: str) -> JobResultInfo | None:
    """Return the result of the most recent job matching a label selector."""
    matching = find_jobs(batch_api, namespace, label_selector)

    if not matching:
        return None

    matching.sort(
        key=lambda j: j.status.start_time if j.status and j.status.start_time else datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )

    latest = matching[0]
    job_name = (latest.metadata.name or "") if latest.metadata else ""
    status = latest.status

    if status and status.active and status.active > 0:
        result_status = "Running"
    elif status and status.succeeded and status.succeeded > 0:
        result_status = "Succeeded"
    elif status and status.failed and status.failed > 0:
        result_status = "Failed"
    else:
        result_status = "Unknown"

    completion = _format_time(status.completion_time if status else None)

    return JobResultInfo(name=job_name, status=result_status, completion_time=completion)


def create_job_from_cronjob(batch_api: BatchV1Api, name: str, namespace: str) -> str:
    """Create a one-off Job from a CronJob's template, return the generated Job name."""
    cj = batch_api.read_namespaced_cron_job(name=name, namespace=namespace)
    job_template = cj.spec.job_template

    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    job_name = f"{name}-manual-{timestamp}"

    template_labels: dict[str, str] = {}
    if job_template.metadata and job_template.metadata.labels:
        template_labels = dict(job_template.metadata.labels)

    job = V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=V1ObjectMeta(
            name=job_name,
            namespace=namespace,
            labels=template_labels,
            annotations={"cronjob.kubernetes.io/instantiate": "manual"},
        ),
        spec=job_template.spec,
    )

    batch_api.create_namespaced_job(namespace=namespace, body=job)
    return job_name
