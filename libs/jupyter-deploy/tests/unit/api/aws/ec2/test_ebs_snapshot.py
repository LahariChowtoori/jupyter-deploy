import unittest
from unittest.mock import Mock, patch

from jupyter_deploy.api.aws.ec2.ebs_snapshot import (
    SNAPSHOT_COMPLETED_STATE,
    SNAPSHOT_ERROR_STATE,
    VOLUME_IDENTITY_TAG,
    create_snapshot,
    delete_snapshot,
    describe_snapshots_by_tags,
    wait_snapshot_completed,
)
from jupyter_deploy.exceptions import JupyterDeployError, ResourceNotFoundError, ResourcePollTimeoutError


def _not_found_error(client_error: type[Exception]) -> Exception:
    error = client_error()
    error.response = {"Error": {"Code": "InvalidSnapshot.NotFound"}}  # type: ignore[attr-defined]
    return error


class TestCreateSnapshot(unittest.TestCase):
    def test_passes_volume_id_and_tags(self) -> None:
        # Setup
        mock_ec2_client = Mock()
        mock_ec2_client.create_snapshot.return_value = {"SnapshotId": "snap-1", "State": "pending"}

        # Execute
        result = create_snapshot(mock_ec2_client, volume_id="vol-1", tags={"DeploymentId": "dep-1"})

        # Verify
        mock_ec2_client.create_snapshot.assert_called_once_with(
            VolumeId="vol-1",
            Description="jupyter-deploy backup of vol-1",
            TagSpecifications=[{"ResourceType": "snapshot", "Tags": [{"Key": "DeploymentId", "Value": "dep-1"}]}],
        )
        self.assertEqual(result["SnapshotId"], "snap-1")

    def test_tags_are_taken_from_the_caller_not_invented(self) -> None:
        """The template owns the tagging convention, so every key must come from the argument."""
        mock_ec2_client = Mock()
        mock_ec2_client.create_snapshot.return_value = {"SnapshotId": "snap-1"}

        create_snapshot(
            mock_ec2_client,
            volume_id="vol-1",
            tags={"DeploymentId": "dep-1", "Source": "jupyter-deploy", VOLUME_IDENTITY_TAG: "home"},
        )

        tags = mock_ec2_client.create_snapshot.call_args.kwargs["TagSpecifications"][0]["Tags"]
        self.assertEqual(
            {tag["Key"] for tag in tags},
            {"DeploymentId", "Source", VOLUME_IDENTITY_TAG},
        )

    def test_no_tags_sends_an_empty_tag_list(self) -> None:
        mock_ec2_client = Mock()
        mock_ec2_client.create_snapshot.return_value = {"SnapshotId": "snap-1"}

        create_snapshot(mock_ec2_client, volume_id="vol-1", tags={})

        tag_specs = mock_ec2_client.create_snapshot.call_args.kwargs["TagSpecifications"]
        self.assertEqual(tag_specs, [{"ResourceType": "snapshot", "Tags": []}])

    def test_an_explicit_description_wins(self) -> None:
        mock_ec2_client = Mock()
        mock_ec2_client.create_snapshot.return_value = {"SnapshotId": "snap-1"}

        create_snapshot(mock_ec2_client, volume_id="vol-1", tags={}, description="before the zone swap")

        self.assertEqual(mock_ec2_client.create_snapshot.call_args.kwargs["Description"], "before the zone swap")

    def test_does_not_wait_for_completion(self) -> None:
        """Returns while the snapshot is still `pending`; waiting is the caller's separate step."""
        mock_ec2_client = Mock()
        mock_ec2_client.create_snapshot.return_value = {"SnapshotId": "snap-1", "State": "pending"}

        result = create_snapshot(mock_ec2_client, volume_id="vol-1", tags={})

        self.assertEqual(result["State"], "pending")
        mock_ec2_client.describe_snapshots.assert_not_called()


class TestWaitSnapshotCompleted(unittest.TestCase):
    @patch("time.sleep")
    def test_returns_as_soon_as_completed(self, mock_sleep: Mock) -> None:
        # Setup
        mock_ec2_client = Mock()
        mock_ec2_client.describe_snapshots.return_value = {
            "Snapshots": [{"SnapshotId": "snap-1", "State": SNAPSHOT_COMPLETED_STATE}]
        }

        # Execute
        result = wait_snapshot_completed(mock_ec2_client, snapshot_id="snap-1")

        # Verify
        self.assertEqual(result["State"], SNAPSHOT_COMPLETED_STATE)
        mock_ec2_client.describe_snapshots.assert_called_once_with(SnapshotIds=["snap-1"])
        mock_sleep.assert_not_called()

    @patch("time.sleep")
    def test_polls_until_completed(self, mock_sleep: Mock) -> None:
        mock_ec2_client = Mock()
        mock_ec2_client.describe_snapshots.side_effect = [
            {"Snapshots": [{"State": "pending", "Progress": "10%"}]},
            {"Snapshots": [{"State": "pending", "Progress": "80%"}]},
            {"Snapshots": [{"State": SNAPSHOT_COMPLETED_STATE}]},
        ]

        wait_snapshot_completed(mock_ec2_client, snapshot_id="snap-1", poll_interval_seconds=7)

        self.assertEqual(mock_ec2_client.describe_snapshots.call_count, 3)
        mock_sleep.assert_called_with(7)

    @patch("time.sleep")
    def test_raises_on_the_error_state(self, mock_sleep: Mock) -> None:
        """A snapshot in `error` will never complete, so waiting on it would burn the whole timeout."""
        mock_ec2_client = Mock()
        mock_ec2_client.describe_snapshots.return_value = {"Snapshots": [{"State": SNAPSHOT_ERROR_STATE}]}

        with self.assertRaises(RuntimeError) as context:
            wait_snapshot_completed(mock_ec2_client, snapshot_id="snap-1")

        self.assertIn("snap-1", str(context.exception))

    @patch("time.sleep")
    def test_raises_resource_not_found_when_the_snapshot_disappears(self, mock_sleep: Mock) -> None:
        mock_ec2_client = Mock()
        mock_ec2_client.describe_snapshots.return_value = {"Snapshots": []}

        with self.assertRaises(ResourceNotFoundError) as context:
            wait_snapshot_completed(mock_ec2_client, snapshot_id="snap-1")

        self.assertEqual(context.exception.resource_name, "snap-1")

    @patch("time.sleep")
    @patch("time.monotonic")
    def test_raises_timeout_error_past_the_deadline(self, mock_monotonic: Mock, mock_sleep: Mock) -> None:
        # Setup: first call sets the deadline, the second is already past it.
        mock_monotonic.side_effect = [0.0, 1000.0]
        mock_ec2_client = Mock()
        mock_ec2_client.describe_snapshots.return_value = {"Snapshots": [{"State": "pending", "Progress": "42%"}]}

        # Execute & Verify
        with self.assertRaises(ResourcePollTimeoutError) as context:
            wait_snapshot_completed(mock_ec2_client, snapshot_id="snap-1", timeout_seconds=900)

        message = str(context.exception)
        self.assertIn("900s", message)
        self.assertIn("42%", message)
        # Typed, so the CLI renders it instead of letting a bare TimeoutError escape as a traceback --
        # which read as "the backup failed" when the snapshot is in fact still being created.
        self.assertIsInstance(context.exception, JupyterDeployError)
        self.assertIsInstance(context.exception, TimeoutError)
        # The hint carries what the message does not: the snapshot continues, and the previous backup is
        # still there. Without that a timeout on a backup reads as "the backup failed".
        hint = str(context.exception.hint)
        self.assertIn("nothing has been lost", hint)
        self.assertIn("jd volume show", hint)

    @patch("time.sleep")
    @patch("time.monotonic")
    def test_checks_the_state_before_the_deadline(self, mock_monotonic: Mock, mock_sleep: Mock) -> None:
        """A snapshot that completed exactly as the clock ran out is a success, not a timeout."""
        mock_monotonic.side_effect = [0.0, 1000.0]
        mock_ec2_client = Mock()
        mock_ec2_client.describe_snapshots.return_value = {"Snapshots": [{"State": SNAPSHOT_COMPLETED_STATE}]}

        result = wait_snapshot_completed(mock_ec2_client, snapshot_id="snap-1", timeout_seconds=900)

        self.assertEqual(result["State"], SNAPSHOT_COMPLETED_STATE)


class TestDescribeSnapshotsByTags(unittest.TestCase):
    def test_pins_the_owner_to_self(self) -> None:
        """A public or shared snapshot carrying the same tags must never be mistaken for ours."""
        mock_ec2_client = Mock()
        mock_ec2_client.describe_snapshots.return_value = {"Snapshots": [{"SnapshotId": "snap-1"}]}

        result = describe_snapshots_by_tags(mock_ec2_client, tag_filters={"DeploymentId": "dep-1"})

        mock_ec2_client.describe_snapshots.assert_called_once_with(
            OwnerIds=["self"], Filters=[{"Name": "tag:DeploymentId", "Values": ["dep-1"]}]
        )
        self.assertEqual(result, [{"SnapshotId": "snap-1"}])

    def test_builds_one_filter_per_tag(self) -> None:
        mock_ec2_client = Mock()
        mock_ec2_client.describe_snapshots.return_value = {"Snapshots": []}

        describe_snapshots_by_tags(mock_ec2_client, tag_filters={"DeploymentId": "dep-1", "Source": "jd"})

        self.assertEqual(
            mock_ec2_client.describe_snapshots.call_args.kwargs["Filters"],
            [
                {"Name": "tag:DeploymentId", "Values": ["dep-1"]},
                {"Name": "tag:Source", "Values": ["jd"]},
            ],
        )

    def test_returns_empty_list_when_nothing_matches(self) -> None:
        mock_ec2_client = Mock()
        mock_ec2_client.describe_snapshots.return_value = {"Snapshots": []}

        self.assertEqual(describe_snapshots_by_tags(mock_ec2_client, tag_filters={"A": "1"}), [])

    def test_returns_empty_list_when_the_key_is_absent(self) -> None:
        mock_ec2_client = Mock()
        mock_ec2_client.describe_snapshots.return_value = {}

        self.assertEqual(describe_snapshots_by_tags(mock_ec2_client, tag_filters={"A": "1"}), [])


class TestDeleteSnapshot(unittest.TestCase):
    def test_deletes_by_id(self) -> None:
        mock_ec2_client = Mock()
        mock_ec2_client.exceptions.ClientError = type("ClientError", (Exception,), {})

        delete_snapshot(mock_ec2_client, snapshot_id="snap-1")

        mock_ec2_client.delete_snapshot.assert_called_once_with(SnapshotId="snap-1")

    def test_an_already_deleted_snapshot_is_success(self) -> None:
        """Idempotence is what makes retrying a partially-failed reap safe."""
        client_error = type("ClientError", (Exception,), {})
        mock_ec2_client = Mock()
        mock_ec2_client.exceptions.ClientError = client_error
        mock_ec2_client.delete_snapshot.side_effect = _not_found_error(client_error)

        delete_snapshot(mock_ec2_client, snapshot_id="snap-1")  # no raise

    def test_any_other_client_error_propagates(self) -> None:
        """A denied permission MUST surface: swallowing it would leak billable snapshots silently."""
        client_error = type("ClientError", (Exception,), {})
        denied = client_error()
        denied.response = {"Error": {"Code": "UnauthorizedOperation"}}  # type: ignore[attr-defined]
        mock_ec2_client = Mock()
        mock_ec2_client.exceptions.ClientError = client_error
        mock_ec2_client.delete_snapshot.side_effect = denied

        with self.assertRaises(client_error):
            delete_snapshot(mock_ec2_client, snapshot_id="snap-1")

    def test_a_client_error_with_no_code_propagates(self) -> None:
        client_error = type("ClientError", (Exception,), {})
        malformed = client_error()
        malformed.response = {}  # type: ignore[attr-defined]
        mock_ec2_client = Mock()
        mock_ec2_client.exceptions.ClientError = client_error
        mock_ec2_client.delete_snapshot.side_effect = malformed

        with self.assertRaises(client_error):
            delete_snapshot(mock_ec2_client, snapshot_id="snap-1")


class TestVolumeIdentityTag(unittest.TestCase):
    def test_the_identity_tag_key_is_pinned(self) -> None:
        """The CLI both writes and reads this key, so a rename must break here, not at restore time."""
        self.assertEqual(VOLUME_IDENTITY_TAG, "VolumeName")
