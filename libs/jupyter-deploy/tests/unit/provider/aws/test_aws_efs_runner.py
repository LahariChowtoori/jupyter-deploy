import json
import unittest
from unittest.mock import Mock, patch

from jupyter_deploy.engine.supervised_execution import NullDisplay
from jupyter_deploy.exceptions import InstructionNotFoundError
from jupyter_deploy.provider.aws.aws_efs_runner import (
    BACKUP_NOT_NEEDED,
    ELASTIC_CAPACITY,
    REGIONAL_ZONE,
    AwsEfsInstruction,
    AwsEfsRunner,
)
from jupyter_deploy.provider.resolved_argdefs import ResolvedInstructionArgument, StrResolvedInstructionArgument


def _ids_arg(value: str) -> dict[str, ResolvedInstructionArgument]:
    """Keyed by the api-attribute name the runner requires.

    The manifest maps `api-attribute: file_system_ids` from `source-key: volume_ids`, so the CLI-side
    param name never reaches the runner -- only the provider-facing attribute does.
    """
    return {"file_system_ids": StrResolvedInstructionArgument(argument_name="file_system_ids", value=value)}


class TestDescribeFileSystems(unittest.TestCase):
    @patch("boto3.client")
    def test_emits_provider_neutral_fields(self, mock_boto: Mock) -> None:
        runner = AwsEfsRunner(NullDisplay(), region_name="us-west-2")
        with patch(
            "jupyter_deploy.api.aws.efs.efs_file_system.describe_file_systems",
            Mock(
                return_value=[
                    {
                        "FileSystemId": "fs-1",
                        "LifeCycleState": "available",
                        "SizeInBytes": {"Value": 2 * 1024**3},
                        "PerformanceMode": "generalPurpose",
                        "Encrypted": True,
                    }
                ]
            ),
        ):
            results = runner.execute_instruction(
                instruction_name=AwsEfsInstruction.DESCRIBE_FILE_SYSTEMS,
                resolved_arguments=_ids_arg("fs-1"),
            )

        payload = json.loads(results["FileSystems"].value)
        self.assertEqual(payload[0]["volume_id"], "fs-1")
        self.assertEqual(payload[0]["state"], "available")
        self.assertTrue(payload[0]["encrypted"])

    @patch("boto3.client")
    def test_capacity_is_elastic_not_a_quantity(self, mock_boto: Mock) -> None:
        """A file system provisions no size, so it says so in words rather than reporting a number."""
        runner = AwsEfsRunner(NullDisplay(), region_name="us-west-2")
        with patch(
            "jupyter_deploy.api.aws.efs.efs_file_system.describe_file_systems",
            Mock(return_value=[{"FileSystemId": "fs-1", "SizeInBytes": {"Value": 2 * 1024**3}}]),
        ):
            results = runner.execute_instruction(
                instruction_name=AwsEfsInstruction.DESCRIBE_FILE_SYSTEMS,
                resolved_arguments=_ids_arg("fs-1"),
            )

        self.assertEqual(json.loads(results["FileSystems"].value)[0]["capacity"], ELASTIC_CAPACITY)

    @patch("boto3.client")
    def test_metered_size_is_never_reported(self, mock_boto: Mock) -> None:
        """Consumed bytes are not a capacity; they belong to a filesystem-stats verb of their own."""
        runner = AwsEfsRunner(NullDisplay(), region_name="us-west-2")
        with patch(
            "jupyter_deploy.api.aws.efs.efs_file_system.describe_file_systems",
            Mock(return_value=[{"FileSystemId": "fs-1", "SizeInBytes": {"Value": 2 * 1024**3}}]),
        ):
            results = runner.execute_instruction(
                instruction_name=AwsEfsInstruction.DESCRIBE_FILE_SYSTEMS,
                resolved_arguments=_ids_arg("fs-1"),
            )

        payload = json.loads(results["FileSystems"].value)[0]
        self.assertNotIn("used", payload)
        self.assertNotIn("2.0Gi", json.dumps(payload))

    @patch("boto3.client")
    def test_zone_says_regional_rather_than_reporting_none(self, mock_boto: Mock) -> None:
        """The word is the answer: being regional is why a zone change cannot threaten this data.

        Not "": an empty zone reads as "not reported yet", a state a user might wait out or try to fix,
        when in fact a file system will never have one.
        """
        runner = AwsEfsRunner(NullDisplay(), region_name="us-west-2")
        with patch(
            "jupyter_deploy.api.aws.efs.efs_file_system.describe_file_systems",
            Mock(return_value=[{"FileSystemId": "fs-1", "LifeCycleState": "available"}]),
        ):
            results = runner.execute_instruction(
                instruction_name=AwsEfsInstruction.DESCRIBE_FILE_SYSTEMS,
                resolved_arguments=_ids_arg("fs-1"),
            )

        payload = json.loads(results["FileSystems"].value)[0]
        self.assertEqual(payload["zone"], REGIONAL_ZONE)
        self.assertNotEqual(payload["zone"], "")

    @patch("boto3.client")
    def test_backup_id_says_not_needed(self, mock_boto: Mock) -> None:
        """The provider answers for the whole kind, so the handler never invents a placeholder.

        Distinct from the "" a block volume reports before its first backup: that one turns into a
        snapshot id as soon as `jd volume backup` runs, whereas this one never changes.
        """
        runner = AwsEfsRunner(NullDisplay(), region_name="us-west-2")
        with patch(
            "jupyter_deploy.api.aws.efs.efs_file_system.describe_file_systems",
            Mock(return_value=[{"FileSystemId": "fs-1", "LifeCycleState": "available"}]),
        ):
            results = runner.execute_instruction(
                instruction_name=AwsEfsInstruction.DESCRIBE_FILE_SYSTEMS,
                resolved_arguments=_ids_arg("fs-1"),
            )

        self.assertEqual(json.loads(results["FileSystems"].value)[0]["backup_id"], BACKUP_NOT_NEEDED)

    @patch("boto3.client")
    def test_splits_and_trims_the_id_list(self, mock_boto: Mock) -> None:
        runner = AwsEfsRunner(NullDisplay(), region_name="us-west-2")
        describe: Mock = Mock(return_value=[])
        with patch("jupyter_deploy.api.aws.efs.efs_file_system.describe_file_systems", describe):
            runner.execute_instruction(
                instruction_name=AwsEfsInstruction.DESCRIBE_FILE_SYSTEMS,
                resolved_arguments=_ids_arg("fs-1, fs-2 ,,"),
            )

        self.assertEqual(describe.call_args.kwargs["file_system_ids"], ["fs-1", "fs-2"])


class TestExecuteInstruction(unittest.TestCase):
    @patch("boto3.client")
    def test_all_instructions_implemented(self, mock_boto: Mock) -> None:
        """Guard: every enum member must dispatch, so a new instruction is exercised here."""
        runner = AwsEfsRunner(NullDisplay(), region_name="us-west-2")
        implemented = {AwsEfsInstruction.DESCRIBE_FILE_SYSTEMS: "_describe_file_systems"}
        self.assertEqual(set(implemented), set(AwsEfsInstruction))

        for instruction, method_name in implemented.items():
            with patch.object(runner, method_name, return_value={}) as mock_method:
                runner.execute_instruction(instruction_name=instruction, resolved_arguments={})
                mock_method.assert_called_once()

    @patch("boto3.client")
    def test_unknown_instruction_raises(self, mock_boto: Mock) -> None:
        runner = AwsEfsRunner(NullDisplay(), region_name="us-west-2")
        with self.assertRaises(InstructionNotFoundError):
            runner.execute_instruction(instruction_name="not-a-thing", resolved_arguments={})
