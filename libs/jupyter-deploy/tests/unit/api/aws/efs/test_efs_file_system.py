import unittest
from unittest.mock import Mock

from jupyter_deploy.api.aws.efs.efs_file_system import FILE_SYSTEM_AVAILABLE_STATE, describe_file_systems


class TestDescribeFileSystems(unittest.TestCase):
    def test_queries_each_id_separately(self) -> None:
        """One call per id: DescribeFileSystems takes a single FileSystemId, not a list.

        Queried by id at all — rather than by tag like the block volumes — because EFS has no tag
        filter on this API. The caller's inventory already knows which file systems are the
        deployment's.
        """
        # Setup
        mock_efs_client = Mock()
        mock_efs_client.exceptions.FileSystemNotFound = type("FileSystemNotFound", (Exception,), {})
        mock_efs_client.describe_file_systems.side_effect = [
            {"FileSystems": [{"FileSystemId": "fs-1"}]},
            {"FileSystems": [{"FileSystemId": "fs-2"}]},
        ]

        # Execute
        result = describe_file_systems(mock_efs_client, file_system_ids=["fs-1", "fs-2"])

        # Verify
        self.assertEqual([fs["FileSystemId"] for fs in result], ["fs-1", "fs-2"])
        self.assertEqual(
            [call.kwargs["FileSystemId"] for call in mock_efs_client.describe_file_systems.call_args_list],
            ["fs-1", "fs-2"],
        )

    def test_skips_a_file_system_that_no_longer_exists(self) -> None:
        """One deleted file system must not fail the whole listing."""
        mock_efs_client = Mock()
        not_found = type("FileSystemNotFound", (Exception,), {})
        mock_efs_client.exceptions.FileSystemNotFound = not_found
        mock_efs_client.describe_file_systems.side_effect = [
            not_found(),
            {"FileSystems": [{"FileSystemId": "fs-2"}]},
        ]

        result = describe_file_systems(mock_efs_client, file_system_ids=["fs-gone", "fs-2"])

        self.assertEqual([fs["FileSystemId"] for fs in result], ["fs-2"])

    def test_all_ids_missing_returns_empty(self) -> None:
        mock_efs_client = Mock()
        not_found = type("FileSystemNotFound", (Exception,), {})
        mock_efs_client.exceptions.FileSystemNotFound = not_found
        mock_efs_client.describe_file_systems.side_effect = not_found()

        self.assertEqual(describe_file_systems(mock_efs_client, file_system_ids=["fs-1", "fs-2"]), [])

    def test_empty_id_list_makes_no_call(self) -> None:
        """A template with no EFS mounts must not reach the provider at all."""
        mock_efs_client = Mock()
        mock_efs_client.exceptions.FileSystemNotFound = type("FileSystemNotFound", (Exception,), {})

        self.assertEqual(describe_file_systems(mock_efs_client, file_system_ids=[]), [])
        mock_efs_client.describe_file_systems.assert_not_called()

    def test_blank_ids_are_skipped(self) -> None:
        """A blank id comes from an output that has not resolved yet, and is not an error."""
        mock_efs_client = Mock()
        mock_efs_client.exceptions.FileSystemNotFound = type("FileSystemNotFound", (Exception,), {})
        mock_efs_client.describe_file_systems.return_value = {"FileSystems": [{"FileSystemId": "fs-1"}]}

        result = describe_file_systems(mock_efs_client, file_system_ids=["", "fs-1", ""])

        self.assertEqual([fs["FileSystemId"] for fs in result], ["fs-1"])
        mock_efs_client.describe_file_systems.assert_called_once_with(FileSystemId="fs-1")

    def test_tolerates_a_response_with_no_file_systems_key(self) -> None:
        mock_efs_client = Mock()
        mock_efs_client.exceptions.FileSystemNotFound = type("FileSystemNotFound", (Exception,), {})
        mock_efs_client.describe_file_systems.return_value = {}

        self.assertEqual(describe_file_systems(mock_efs_client, file_system_ids=["fs-1"]), [])

    def test_returns_every_description_verbatim(self) -> None:
        """The api layer does not reshape: the runner owns the translation to neutral field names."""
        mock_efs_client = Mock()
        mock_efs_client.exceptions.FileSystemNotFound = type("FileSystemNotFound", (Exception,), {})
        described = {
            "FileSystemId": "fs-1",
            "LifeCycleState": FILE_SYSTEM_AVAILABLE_STATE,
            "SizeInBytes": {"Value": 1024},
            "PerformanceMode": "generalPurpose",
            "Encrypted": True,
        }
        mock_efs_client.describe_file_systems.return_value = {"FileSystems": [described]}

        self.assertEqual(describe_file_systems(mock_efs_client, file_system_ids=["fs-1"]), [described])

    def test_any_other_error_propagates(self) -> None:
        """Only FileSystemNotFound is tolerated; a denied permission must surface."""
        mock_efs_client = Mock()
        mock_efs_client.exceptions.FileSystemNotFound = type("FileSystemNotFound", (Exception,), {})
        mock_efs_client.describe_file_systems.side_effect = ValueError("access denied")

        with self.assertRaises(ValueError):
            describe_file_systems(mock_efs_client, file_system_ids=["fs-1"])


class TestFileSystemAvailableState(unittest.TestCase):
    def test_available_is_the_usable_state(self) -> None:
        """Unlike a block volume, a file system has no attachment state.

        Reachability is a property of its per-zone mount targets, not of the file system, which is why
        the runner reports no `zone` for it.
        """
        self.assertEqual(FILE_SYSTEM_AVAILABLE_STATE, "available")
