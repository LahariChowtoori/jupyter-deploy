"""Tests for the `jd volume` reading helpers."""

import json
import unittest
from unittest.mock import Mock

from pytest_jupyter_deploy.volumes import get_volume_details, get_volume_names


def _deployment(stdout: str) -> Mock:
    deployment = Mock()
    deployment.cli.run_command.return_value = Mock(stdout=stdout)
    return deployment


class TestGetVolumeNames(unittest.TestCase):
    def test_projects_names_out_of_the_object_list(self) -> None:
        """`volume list --json` emits {name, type, description} objects, like `component list`."""
        deployment = _deployment(
            json.dumps(
                [
                    {"name": "home", "type": "ebs", "description": "JupyterLab home volume"},
                    {"name": "home/data", "type": "efs", "description": "data"},
                ]
            )
        )

        self.assertEqual(get_volume_names(deployment), ["home", "home/data"])
        deployment.cli.run_command.assert_called_once_with(["jupyter-deploy", "volume", "list", "--json"])

    def test_tolerates_an_empty_inventory(self) -> None:
        self.assertEqual(get_volume_names(_deployment(json.dumps([]))), [])


class TestGetVolumeDetails(unittest.TestCase):
    def test_passes_the_name_through(self) -> None:
        deployment = _deployment(json.dumps({"name": "home/data"}))

        self.assertEqual(get_volume_details(deployment, "home/data")["name"], "home/data")
        deployment.cli.run_command.assert_called_once_with(
            ["jupyter-deploy", "volume", "show", "--json", "--name", "home/data"]
        )

    def test_omits_name_entirely_when_none(self) -> None:
        """Not `--name ""`: the manifest owns the default, so the flag must be absent, not empty."""
        deployment = _deployment(json.dumps({"name": "home"}))

        get_volume_details(deployment)

        deployment.cli.run_command.assert_called_once_with(["jupyter-deploy", "volume", "show", "--json"])

    def test_absent_capacity_is_simply_missing(self) -> None:
        """`capacity` is omitted rather than empty when the provider reported no size."""
        details = get_volume_details(_deployment(json.dumps({"name": "home", "zone": "us-west-2a"})))

        self.assertNotIn("capacity", details)
