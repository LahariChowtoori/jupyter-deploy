from __future__ import annotations

from mypy_boto3_efs.client import EFSClient
from mypy_boto3_efs.type_defs import FileSystemDescriptionTypeDef

# An EFS file system reports `available` once usable. Unlike a block volume it has no attachment state:
# reachability is a property of its per-zone mount targets, not of the file system itself.
FILE_SYSTEM_AVAILABLE_STATE = "available"


def describe_file_systems(ec2_client: EFSClient, file_system_ids: list[str]) -> list[FileSystemDescriptionTypeDef]:
    """Describe specific file systems, skipping any that no longer exist."""
    described: list[FileSystemDescriptionTypeDef] = []

    for file_system_id in file_system_ids:
        if not file_system_id:
            continue
        try:
            response = ec2_client.describe_file_systems(FileSystemId=file_system_id)
        except ec2_client.exceptions.FileSystemNotFound:
            # Deleted out of band, or not yet created. The caller reports it as unknown rather than
            # failing the whole listing for one missing entry.
            continue
        described.extend(response.get("FileSystems", []))

    return described
