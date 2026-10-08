import unittest
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from kubernetes.client import BatchV1Api, V1JobSpec, V1PodTemplateSpec

from jupyter_deploy.api.k8s.batch import (
    CronJobInfo,
    CronJobStatus,
    JobResultInfo,
    create_job_from_cronjob,
    find_jobs,
    get_cronjob,
    get_cronjob_status,
    get_last_job_result,
)


def _mock_cronjob(
    name: str = "jwt-rotator",
    schedule: str = "0 */6 * * *",
    suspended: bool = False,
    active_count: int = 0,
) -> Mock:
    cj: Mock = Mock()
    cj.metadata.name = name
    cj.spec.schedule = schedule
    cj.spec.suspend = suspended
    # kubernetes v37 validates V1Job fields, so the job spec must be a real model
    cj.spec.job_template.spec = V1JobSpec(template=V1PodTemplateSpec())
    cj.spec.job_template.metadata = Mock()
    cj.spec.job_template.metadata.labels = {"app": name}
    cj.status.last_schedule_time = datetime(2025, 5, 14, 6, 0, 0, tzinfo=UTC)
    cj.status.last_successful_time = datetime(2025, 5, 14, 6, 0, 30, tzinfo=UTC)
    cj.status.active = [Mock()] * active_count if active_count else None
    return cj


class TestGetCronjobStatus(unittest.TestCase):
    def test_returns_idle_when_no_active_jobs(self) -> None:
        mock_api: Mock = Mock(spec=BatchV1Api)
        mock_api.read_namespaced_cron_job.return_value = _mock_cronjob()

        result = get_cronjob_status(mock_api, name="jwt-rotator", namespace="router")

        self.assertIsInstance(result, CronJobStatus)
        self.assertEqual(result.name, "jwt-rotator")
        self.assertEqual(result.schedule, "0 */6 * * *")
        self.assertFalse(result.suspended)
        self.assertEqual(result.active_count, 0)

    def test_returns_suspended_status(self) -> None:
        mock_api: Mock = Mock(spec=BatchV1Api)
        mock_api.read_namespaced_cron_job.return_value = _mock_cronjob(suspended=True)

        result = get_cronjob_status(mock_api, name="jwt-rotator", namespace="router")

        self.assertTrue(result.suspended)

    def test_returns_active_count(self) -> None:
        mock_api: Mock = Mock(spec=BatchV1Api)
        mock_api.read_namespaced_cron_job.return_value = _mock_cronjob(active_count=2)

        result = get_cronjob_status(mock_api, name="jwt-rotator", namespace="router")

        self.assertEqual(result.active_count, 2)


class TestGetCronjob(unittest.TestCase):
    @patch("jupyter_deploy.api.k8s.batch.ApiClient")
    def test_returns_cronjob_info(self, mock_api_client_cls: Mock) -> None:
        mock_api_client_cls.return_value.sanitize_for_serialization.return_value = {"kind": "CronJob"}
        mock_api: Mock = Mock(spec=BatchV1Api)
        mock_api.read_namespaced_cron_job.return_value = _mock_cronjob()

        result = get_cronjob(mock_api, name="jwt-rotator", namespace="router")

        self.assertIsInstance(result, CronJobInfo)
        self.assertEqual(result.name, "jwt-rotator")
        self.assertEqual(result.schedule, "0 */6 * * *")
        self.assertIsInstance(result.resource, dict)


class TestCreateJobFromCronjob(unittest.TestCase):
    def test_creates_job_with_manual_prefix(self) -> None:
        mock_api: Mock = Mock(spec=BatchV1Api)
        mock_api.read_namespaced_cron_job.return_value = _mock_cronjob()

        job_name = create_job_from_cronjob(mock_api, name="jwt-rotator", namespace="router")

        self.assertTrue(job_name.startswith("jwt-rotator-manual-"))
        mock_api.create_namespaced_job.assert_called_once()

    def test_created_job_copies_template_labels(self) -> None:
        mock_api: Mock = Mock(spec=BatchV1Api)
        mock_api.read_namespaced_cron_job.return_value = _mock_cronjob()

        create_job_from_cronjob(mock_api, name="jwt-rotator", namespace="router")

        call_kwargs = mock_api.create_namespaced_job.call_args
        job_body = call_kwargs.kwargs.get("body") or call_kwargs[1]["body"]
        labels = job_body.metadata.labels
        self.assertEqual(labels["app"], "jwt-rotator")


def _mock_job(name: str, start_time: datetime, succeeded: int = 0, failed: int = 0, active: int = 0) -> Mock:
    job: Mock = Mock()
    job.metadata.name = name
    job.status.start_time = start_time
    job.status.succeeded = succeeded
    job.status.failed = failed
    job.status.active = active
    job.status.completion_time = datetime(2025, 5, 14, 6, 1, 0, tzinfo=UTC) if succeeded else None
    return job


class TestFindJobs(unittest.TestCase):
    def test_returns_matching_jobs(self) -> None:
        mock_api: Mock = Mock(spec=BatchV1Api)
        job = _mock_job("jwt-rotator-123", datetime(2025, 5, 14, 6, 0, 0, tzinfo=UTC), succeeded=1)
        mock_api.list_namespaced_job.return_value = Mock(items=[job])

        result = find_jobs(mock_api, namespace="router", label_selector="app=jwt-rotator")

        self.assertEqual(len(result), 1)
        mock_api.list_namespaced_job.assert_called_once_with(namespace="router", label_selector="app=jwt-rotator")

    def test_returns_empty_when_no_jobs(self) -> None:
        mock_api: Mock = Mock(spec=BatchV1Api)
        mock_api.list_namespaced_job.return_value = Mock(items=[])

        result = find_jobs(mock_api, namespace="router", label_selector="app=jwt-rotator")

        self.assertEqual(result, [])


class TestGetLastJobResult(unittest.TestCase):
    def test_returns_none_when_no_jobs(self) -> None:
        mock_api: Mock = Mock(spec=BatchV1Api)
        mock_api.list_namespaced_job.return_value = Mock(items=[])

        result = get_last_job_result(mock_api, namespace="router", label_selector="app=jwt-rotator")

        self.assertIsNone(result)

    def test_returns_succeeded_for_completed_job(self) -> None:
        mock_api: Mock = Mock(spec=BatchV1Api)
        job = _mock_job("jwt-rotator-123", datetime(2025, 5, 14, 6, 0, 0, tzinfo=UTC), succeeded=1)
        mock_api.list_namespaced_job.return_value = Mock(items=[job])

        result = get_last_job_result(mock_api, namespace="router", label_selector="app=jwt-rotator")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsInstance(result, JobResultInfo)
        self.assertEqual(result.status, "Succeeded")
        self.assertEqual(result.name, "jwt-rotator-123")

    def test_returns_running_for_active_job(self) -> None:
        mock_api: Mock = Mock(spec=BatchV1Api)
        job = _mock_job("jwt-rotator-manual-456", datetime(2025, 5, 14, 6, 0, 0, tzinfo=UTC), active=1)
        mock_api.list_namespaced_job.return_value = Mock(items=[job])

        result = get_last_job_result(mock_api, namespace="router", label_selector="app=jwt-rotator")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "Running")

    def test_returns_failed_for_failed_job(self) -> None:
        mock_api: Mock = Mock(spec=BatchV1Api)
        job = _mock_job("jwt-rotator-789", datetime(2025, 5, 14, 6, 0, 0, tzinfo=UTC), failed=1)
        mock_api.list_namespaced_job.return_value = Mock(items=[job])

        result = get_last_job_result(mock_api, namespace="router", label_selector="app=jwt-rotator")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "Failed")

    def test_picks_most_recent_job(self) -> None:
        mock_api: Mock = Mock(spec=BatchV1Api)
        older = _mock_job("jwt-rotator-old", datetime(2025, 5, 14, 3, 0, 0, tzinfo=UTC), succeeded=1)
        newer = _mock_job("jwt-rotator-new", datetime(2025, 5, 14, 6, 0, 0, tzinfo=UTC), failed=1)
        mock_api.list_namespaced_job.return_value = Mock(items=[older, newer])

        result = get_last_job_result(mock_api, namespace="router", label_selector="app=jwt-rotator")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.name, "jwt-rotator-new")
        self.assertEqual(result.status, "Failed")
