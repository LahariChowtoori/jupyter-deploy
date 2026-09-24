import json
from enum import Enum

import boto3
from mypy_boto3_efs.client import EFSClient

from jupyter_deploy.api.aws.efs import efs_file_system
from jupyter_deploy.engine.supervised_execution import DisplayManager
from jupyter_deploy.exceptions import InstructionNotFoundError
from jupyter_deploy.provider.instruction_runner import InstructionRunner
from jupyter_deploy.provider.resolved_argdefs import (
    ResolvedInstructionArgument,
    StrResolvedInstructionArgument,
    require_arg,
)
from jupyter_deploy.provider.resolved_resultdefs import (
    ResolvedInstructionResult,
    StrResolvedInstructionResult,
)

# Reported as the capacity of a file system, in place of the quantity a block volume reports. Grows on
# demand, so no number would be true for longer than it took to read it.
ELASTIC_CAPACITY = "elastic"

# Reported as the zone of a file system, in place of the availability zone a block volume sits in. A word
# describing what the file system IS, rather than a placeholder: being regional is precisely the reason a
# zone change relocates its mount target instead of threatening its data.
REGIONAL_ZONE = "regional"

# Reported in place of a backup id. Distinct from the "" a block volume reports before its first backup:
# that one becomes a snapshot id as soon as `jd volume backup` runs, whereas this never changes. Says
# `not needed` rather than `none`, because the reason there is no backup is that a regional file system
# cannot be lost to the zone change backups exist to survive.
BACKUP_NOT_NEEDED = "not needed"


class AwsEfsInstruction(str, Enum):
    """AWS EFS instructions accessible from manifest.commands[].sequence[].api-name."""

    DESCRIBE_FILE_SYSTEMS = "describe-file-systems"


class AwsEfsRunner(InstructionRunner):
    """Runner class for AWS EFS service API instructions."""

    client: EFSClient

    def __init__(self, display_manager: DisplayManager, region_name: str | None) -> None:
        """Instantiates the EFS boto3 client."""
        super().__init__(display_manager)
        self.client: EFSClient = boto3.client("efs", region_name=region_name)

    def _describe_file_systems(
        self,
        resolved_arguments: dict[str, ResolvedInstructionArgument],
    ) -> dict[str, ResolvedInstructionResult]:
        raw_ids = require_arg(resolved_arguments, "file_system_ids", StrResolvedInstructionArgument).value
        file_system_ids = [fs_id.strip() for fs_id in raw_ids.split(",") if fs_id.strip()]

        described = efs_file_system.describe_file_systems(self.client, file_system_ids=file_system_ids)

        # Same JSON-string result shape and the same provider-neutral field names as the block-volume
        # listing, so one handler can join either kind by volume_id without special-casing.
        #
        # Three fields carry a WORD where a block volume carries a value, and all three say the same thing
        # from different angles: this kind of storage has no such quantity, ever. Answering "" instead would
        # be read as "not reported yet" -- a temporary state the user might wait out or try to fix.
        #
        # `capacity` is ELASTIC rather than a quantity: a file system provisions none, so there is no number
        # to report. EFS does meter consumed bytes, but that is not a capacity -- putting it here would make
        # one field name mean "provisioned" for a block volume and "consumed" here. Consumption belongs to a
        # filesystem-stats verb of its own, which can also answer it for storage this listing has no size
        # API for at all. Anything reading capacity as a quantity must tolerate this value.
        return {
            "FileSystems": StrResolvedInstructionResult(
                result_name="FileSystems",
                value=json.dumps(
                    [
                        {
                            "volume_id": fs.get("FileSystemId", ""),
                            "zone": REGIONAL_ZONE,
                            "backup_id": BACKUP_NOT_NEEDED,
                            "capacity": ELASTIC_CAPACITY,
                            "volume_type": fs.get("PerformanceMode", ""),
                            "state": fs.get("LifeCycleState", ""),
                            "encrypted": bool(fs.get("Encrypted", False)),
                        }
                        for fs in described
                    ]
                ),
            )
        }

    def execute_instruction(
        self,
        instruction_name: str,
        resolved_arguments: dict[str, ResolvedInstructionArgument],
    ) -> dict[str, ResolvedInstructionResult]:
        if instruction_name == AwsEfsInstruction.DESCRIBE_FILE_SYSTEMS:
            return self._describe_file_systems(resolved_arguments=resolved_arguments)

        raise InstructionNotFoundError(f"No execution implementation for command: 'aws.efs.{instruction_name}'")
