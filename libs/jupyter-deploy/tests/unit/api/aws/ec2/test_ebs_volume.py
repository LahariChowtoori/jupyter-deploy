import unittest
from unittest.mock import Mock

from jupyter_deploy.api.aws.ec2.ebs_volume import VOLUME_IN_USE_STATE, describe_volumes_by_tags


class TestDescribeVolumesByTags(unittest.TestCase):
    def test_builds_one_tag_filter_per_key(self) -> None:
        # Setup
        mock_ec2_client = Mock()
        mock_ec2_client.describe_volumes.return_value = {"Volumes": [{"VolumeId": "vol-1"}]}

        # Execute
        result = describe_volumes_by_tags(mock_ec2_client, tag_filters={"DeploymentId": "dep-1", "Source": "jd"})

        # Verify
        mock_ec2_client.describe_volumes.assert_called_once_with(
            Filters=[
                {"Name": "tag:DeploymentId", "Values": ["dep-1"]},
                {"Name": "tag:Source", "Values": ["jd"]},
            ]
        )
        self.assertEqual(result, [{"VolumeId": "vol-1"}])

    def test_filters_are_anded_not_ored(self) -> None:
        """Every tag is a separate Filter entry, which EC2 evaluates as AND.

        Collapsing them into one entry with two Values would match a volume carrying EITHER tag, so a
        second deployment's volume could be reported as this one's.
        """
        mock_ec2_client = Mock()
        mock_ec2_client.describe_volumes.return_value = {"Volumes": []}

        describe_volumes_by_tags(mock_ec2_client, tag_filters={"A": "1", "B": "2"})

        filters = mock_ec2_client.describe_volumes.call_args.kwargs["Filters"]
        self.assertEqual(len(filters), 2)
        for entry in filters:
            self.assertEqual(len(entry["Values"]), 1)

    def test_returns_empty_list_when_nothing_matches(self) -> None:
        mock_ec2_client = Mock()
        mock_ec2_client.describe_volumes.return_value = {"Volumes": []}

        self.assertEqual(describe_volumes_by_tags(mock_ec2_client, tag_filters={"DeploymentId": "dep-1"}), [])

    def test_returns_empty_list_when_the_key_is_absent(self) -> None:
        """A response with no Volumes key must not raise: the caller treats absence as "none"."""
        mock_ec2_client = Mock()
        mock_ec2_client.describe_volumes.return_value = {}

        self.assertEqual(describe_volumes_by_tags(mock_ec2_client, tag_filters={"DeploymentId": "dep-1"}), [])

    def test_no_tag_filters_sends_no_filters(self) -> None:
        mock_ec2_client = Mock()
        mock_ec2_client.describe_volumes.return_value = {"Volumes": []}

        describe_volumes_by_tags(mock_ec2_client, tag_filters={})

        mock_ec2_client.describe_volumes.assert_called_once_with(Filters=[])

    def test_returns_every_matching_volume(self) -> None:
        mock_ec2_client = Mock()
        mock_ec2_client.describe_volumes.return_value = {"Volumes": [{"VolumeId": "vol-1"}, {"VolumeId": "vol-2"}]}

        result = describe_volumes_by_tags(mock_ec2_client, tag_filters={"DeploymentId": "dep-1"})

        self.assertEqual([v["VolumeId"] for v in result], ["vol-1", "vol-2"])

    def test_raises_when_the_api_raises(self) -> None:
        mock_ec2_client = Mock()
        mock_ec2_client.describe_volumes.side_effect = ValueError("boom")

        with self.assertRaises(ValueError):
            describe_volumes_by_tags(mock_ec2_client, tag_filters={"DeploymentId": "dep-1"})


class TestVolumeInUseState(unittest.TestCase):
    def test_in_use_is_the_attached_state(self) -> None:
        """Pinned because the name misleads: a STOPPED instance still reports its volumes `in-use`.

        Code that read `in-use` as "the instance is running" would be wrong, which is why the runner
        translates this to `attached` rather than passing the AWS word through.
        """
        self.assertEqual(VOLUME_IN_USE_STATE, "in-use")
