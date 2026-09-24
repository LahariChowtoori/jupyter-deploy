from __future__ import annotations

import time

from mypy_boto3_ec2.client import EC2Client
from mypy_boto3_ec2.type_defs import FilterTypeDef, SnapshotResponseTypeDef, SnapshotTypeDef, TagTypeDef

from jupyter_deploy.exceptions import ResourceNotFoundError, ResourcePollTimeoutError

# Tag recording which volume identity a backup belongs to. The CLI both WRITES and READS this key, so
# unlike the tags a template stamps on its own resources it cannot drift: it never leaves this codebase.
VOLUME_IDENTITY_TAG = "VolumeName"

# A snapshot is usable as a volume source only once it reports `completed`.
SNAPSHOT_COMPLETED_STATE = "completed"
SNAPSHOT_ERROR_STATE = "error"


def create_snapshot(
    ec2_client: EC2Client,
    volume_id: str,
    tags: dict[str, str],
    description: str = "",
) -> SnapshotResponseTypeDef:
    """Create a snapshot of one EBS volume, tagged with the caller-supplied tags.

    Tag keys are NOT defined here: the template owns the tagging convention and passes it down, so a
    backup carries the same keys as every other resource of the deployment.

    Returns immediately: the snapshot is `pending` and must reach `completed` before a volume can be
    created from it. Use wait_snapshot_completed().
    """
    tag_specs: list[TagTypeDef] = [{"Key": key, "Value": value} for key, value in tags.items()]

    # `ResourceType` is not a tag -- it is the required discriminator of each TagSpecifications entry.
    # TagSpecifications is a LIST because several EC2 calls create more than one kind of resource in one
    # request (RunInstances makes an instance, its volumes and its ENIs, each tagged by its own entry), so
    # every entry has to name the kind it targets. CreateSnapshot makes one kind, hence one entry, and
    # `snapshot` is the only value accepted -- EC2's enum has no `ebs-` prefix because the EBS APIs live in
    # the EC2 namespace.
    #
    # Load-bearing, not decorative: omit it and the snapshot is created UNTAGGED, which makes it invisible
    # to `volume.backups` (it filters on tag:DeploymentId) and to the destroy-time reaper -- so backups
    # would quietly stop being discoverable and would leak on teardown.
    return ec2_client.create_snapshot(
        VolumeId=volume_id,
        Description=description or f"jupyter-deploy backup of {volume_id}",
        TagSpecifications=[{"ResourceType": "snapshot", "Tags": tag_specs}],
    )


def wait_snapshot_completed(
    ec2_client: EC2Client,
    snapshot_id: str,
    timeout_seconds: int = 900,
    poll_interval_seconds: int = 5,
) -> SnapshotTypeDef:
    """Block until the snapshot reports `completed`.

    Raises:
        ResourceNotFoundError: If the snapshot disappears while waiting.
        ResourcePollTimeoutError: If it does not complete within timeout_seconds. The snapshot keeps
            being created; the caller then leaves the superseded backup in place.
        RuntimeError: If it enters the `error` state.
    """
    deadline = time.monotonic() + timeout_seconds

    while True:
        response = ec2_client.describe_snapshots(SnapshotIds=[snapshot_id])
        snapshots = response.get("Snapshots", [])

        if not snapshots:
            raise ResourceNotFoundError(
                "snapshot",
                snapshot_id,
                "describe_snapshots returned no snapshot while waiting for it to complete",
            )

        snapshot = snapshots[0]
        state = snapshot.get("State", "")

        if state == SNAPSHOT_COMPLETED_STATE:
            return snapshot
        if state == SNAPSHOT_ERROR_STATE:
            raise RuntimeError(f"Snapshot {snapshot_id} entered the 'error' state and cannot be used.")

        if time.monotonic() >= deadline:
            # The snapshot keeps going without us: EBS fixes its CONTENT at the moment it was requested,
            # so waiting only buys the right to delete the superseded backup, which the caller skips.
            progress = snapshot.get("Progress", "unknown")
            raise ResourcePollTimeoutError(
                "volume backup",
                snapshot_id,
                f"{state} ({progress} done after {timeout_seconds}s)",
                # Says only what this layer knows: the snapshot's own fate. Whether an older backup
                # survived is the caller's ordering, not ours -- `backup_volume` happens to delete the
                # superseded one only after a successful wait, but nothing here can assert that.
                hint="The snapshot is still being created and nothing has been lost. Run 'jd volume show' "
                "to see when it reports 'completed'.",
            )

        time.sleep(poll_interval_seconds)


def describe_snapshots_by_tags(ec2_client: EC2Client, tag_filters: dict[str, str]) -> list[SnapshotTypeDef]:
    """Return the caller's snapshots matching every supplied tag.

    Owner is pinned to self: a shared or public snapshot carrying the same tags must never be mistaken
    for one of ours.
    """
    filters: list[FilterTypeDef] = [{"Name": f"tag:{key}", "Values": [value]} for key, value in tag_filters.items()]
    response = ec2_client.describe_snapshots(OwnerIds=["self"], Filters=filters)
    return list(response.get("Snapshots", []))


def delete_snapshot(ec2_client: EC2Client, snapshot_id: str) -> None:
    """Delete one snapshot.

    An already-deleted snapshot is treated as success: callers reap in a loop and may race a concurrent
    reap or a manual delete, and idempotence is what makes retrying a partially-failed reap safe.
    """
    try:
        ec2_client.delete_snapshot(SnapshotId=snapshot_id)
    except ec2_client.exceptions.ClientError as e:
        if e.response.get("Error", {}).get("Code", "") == "InvalidSnapshot.NotFound":
            return
        raise
