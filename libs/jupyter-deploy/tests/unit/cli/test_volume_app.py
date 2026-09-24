import json
import unittest
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from jupyter_deploy.cli.volume_app import volume_app
from jupyter_deploy.exceptions import VolumeNotBackupableError, VolumeNotFoundError
from jupyter_deploy.handlers.payloads import VolumeBackupResult, VolumeDetail, VolumeInfo

_INFO = VolumeInfo(name="home", volume_class="ebs", description="JupyterLab home volume")
_EFS_INFO = VolumeInfo(name="home/shared", volume_class="efs", description="shared")

_DETAIL = VolumeDetail(
    name="home",
    volume_id="vol-home",
    mount_point="/home/jovyan",
    description="JupyterLab home volume",
    zone="us-west-2a",
    capacity="30Gi",
    volume_class="ebs",
    volume_type="gp3",
    encrypted=True,
    backup_id="snap-home",
    backup_state="completed",
    backup_timestamp="2026-09-16T10:00:00+00:00",
)


class TestVolumeBackupArgumentChecks(unittest.TestCase):
    """--name and --all are mutually exclusive, and one of them is required."""

    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_name_and_all_together_is_rejected(self) -> None:
        result = self.runner.invoke(volume_app, ["backup", "--name", "home", "--all"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("not both", result.output)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_neither_name_nor_all_backs_up_the_default_volume(
        self, mock_handler_cls: Mock, mock_project_dir: Mock
    ) -> None:
        """Bare `backup` is the primary volume, not an error: it is the one a user means by default."""
        handler = mock_handler_cls.return_value
        handler.backup_volume.return_value = VolumeBackupResult(
            name="home", volume_id="vol-home", backup_id="snap-1", state="completed", superseded_backup_ids=[]
        )

        result = self.runner.invoke(volume_app, ["backup"])

        self.assertEqual(result.exit_code, 0)
        # None, not "": the handler resolves the default from the manifest, so the CLI must not guess.
        handler.backup_volume.assert_called_once_with(None)
        handler.backup_all.assert_not_called()

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_all_backs_up_every_volume(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        handler = mock_handler_cls.return_value
        handler.backup_all.return_value = [
            VolumeBackupResult(
                name="home", volume_id="vol-home", backup_id="snap-1", state="completed", superseded_backup_ids=[]
            )
        ]

        result = self.runner.invoke(volume_app, ["backup", "--all"])

        self.assertEqual(result.exit_code, 0)
        handler.backup_all.assert_called_once()
        handler.backup_volume.assert_not_called()

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_name_backs_up_only_that_volume(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        handler = mock_handler_cls.return_value
        handler.backup_volume.return_value = VolumeBackupResult(
            name="home", volume_id="vol-home", backup_id="snap-1", state="completed", superseded_backup_ids=[]
        )

        result = self.runner.invoke(volume_app, ["backup", "--name", "home"])

        self.assertEqual(result.exit_code, 0)
        handler.backup_volume.assert_called_once_with("home")
        handler.backup_all.assert_not_called()


class TestVolumeBackupRefusal(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_unbackupable_volume_exits_with_the_reason_and_hint(
        self, mock_handler_cls: Mock, mock_project_dir: Mock
    ) -> None:
        """Asking to back up a filesystem must fail cleanly, not traceback: the decorator must catch it."""
        mock_handler_cls.return_value.backup_volume.side_effect = VolumeNotBackupableError(
            "home/shared",
            "the template declares no backup mechanism for 'efs' storage",
            volume_class="efs",
            hint="Run 'jd volume backup --all' to back up every volume that supports it.",
        )

        result = self.runner.invoke(volume_app, ["backup", "--name", "home/shared"])

        self.assertEqual(result.exit_code, 1)
        # Substrings kept short: rich wraps the message at the console width, so a long one straddles a
        # newline and never matches.
        self.assertIn("cannot be backed up", result.output)
        self.assertIn("'efs' storage", result.output)
        self.assertIn("--all", result.output)
        self.assertNotIn("Traceback", result.output)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_unknown_volume_lists_the_mounted_ones(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        """A typo gets the valid names back, the way `jd image` and `jd component` already answer.

        Only the error decorator renders that list, so a handler raising the provider-layer
        `ResourceNotFoundError` instead would still exit 1 with a clean message and lose it silently.
        """
        mock_handler_cls.return_value.backup_volume.side_effect = VolumeNotFoundError(
            "hme", ["home", "home/external-ebs1"]
        )

        result = self.runner.invoke(volume_app, ["backup", "--name", "hme"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("not found", result.output)
        self.assertIn("home/external-ebs1", result.output)
        self.assertIn("volume list", result.output)
        self.assertNotIn("Traceback", result.output)


class TestVolumeList(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_list_renders_a_table_with_the_volume_class(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        """Name / Class / Description, shaped like `jd component list` but not named like it.

        `Class` is the column that earns the table: it says whether a volume can be backed up at all,
        which a list of bare names would hide. Headed `Class` rather than `Type` because a volume also has
        a provider "volume type" (`gp3`), which `show` reports separately.
        """
        mock_handler_cls.return_value.list_volumes.return_value = [_INFO, _EFS_INFO]

        result = self.runner.invoke(volume_app, ["list"])

        self.assertEqual(result.exit_code, 0)
        for expected in ("Name", "Class", "Description", "home", "ebs", "home/shared", "efs"):
            self.assertIn(expected, result.output)
        self.assertNotIn("Type", result.output)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_list_json_matches_the_component_shape(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        """A list of {name, class, description} objects, shaped like `component list --json`."""
        mock_handler_cls.return_value.list_volumes.return_value = [_INFO]

        result = self.runner.invoke(volume_app, ["list", "--json"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            json.loads(result.output),
            [{"name": "home", "volume_class": "ebs", "description": "JupyterLab home volume"}],
        )

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_list_renders_an_empty_table_when_there_are_no_volumes(
        self, mock_handler_cls: Mock, mock_project_dir: Mock
    ) -> None:
        """Headers with no rows, matching `component list` — not a "None" placeholder."""
        mock_handler_cls.return_value.list_volumes.return_value = []

        result = self.runner.invoke(volume_app, ["list"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Name", result.output)
        self.assertNotIn("home", result.output)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_list_text_is_comma_separated_names(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        mock_handler_cls.return_value.list_volumes.return_value = [_INFO, _EFS_INFO]

        result = self.runner.invoke(volume_app, ["list", "--text"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output.strip(), "home,home/shared")

    def test_list_json_and_text_together_is_rejected(self) -> None:
        result = self.runner.invoke(volume_app, ["list", "--json", "--text"])

        self.assertEqual(result.exit_code, 1)


class TestVolumeShow(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_show_json_is_parseable(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        mock_handler_cls.return_value.show_volume.return_value = _DETAIL

        result = self.runner.invoke(volume_app, ["show", "--name", "home", "--json"])

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.output)
        self.assertEqual(payload["name"], "home")
        self.assertEqual(payload["backup_id"], "snap-home")
        self.assertEqual(payload["capacity"], "30Gi")

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_show_without_a_name_defers_to_the_handler(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        """--name is optional; the manifest decides the default, so the CLI passes None through."""
        mock_handler_cls.return_value.show_volume.return_value = _DETAIL

        result = self.runner.invoke(volume_app, ["show"])

        self.assertEqual(result.exit_code, 0)
        mock_handler_cls.return_value.show_volume.assert_called_once_with(None)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_show_json_omits_an_unreported_capacity(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        """Absent, not "": a key holding an empty size would read as a volume of size zero."""
        detail = VolumeDetail(**{**vars(_DETAIL), "capacity": None})
        mock_handler_cls.return_value.show_volume.return_value = detail

        result = self.runner.invoke(volume_app, ["show", "--name", "home", "--json"])

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("capacity", json.loads(result.output))

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_show_json_omits_the_backup_fields_when_there_is_no_backup(
        self, mock_handler_cls: Mock, mock_project_dir: Mock
    ) -> None:
        """An EFS mount's rendering: a `backup_id` that says so, and no state or timestamp keys at all."""
        detail = VolumeDetail(
            **{
                **vars(_DETAIL),
                "zone": "regional",
                "capacity": "elastic",
                "volume_class": "efs",
                "volume_type": "generalPurpose",
                "backup_id": "not needed",
                "backup_state": None,
                "backup_timestamp": None,
            }
        )
        mock_handler_cls.return_value.show_volume.return_value = detail

        result = self.runner.invoke(volume_app, ["show", "--name", "home/shared", "--json"])

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.output)
        self.assertEqual(payload["zone"], "regional")
        self.assertEqual(payload["backup_id"], "not needed")
        self.assertNotIn("backup_state", payload)
        self.assertNotIn("backup_timestamp", payload)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_show_json_reports_the_class_and_the_tier_separately(
        self, mock_handler_cls: Mock, mock_project_dir: Mock
    ) -> None:
        """Two keys, not one label: `volume_class` is the class, `volume_type` the tier within it.

        `volume_type` alone is undecodable -- `generalPurpose` and `gp3` say nothing about which storage
        service answered. Merging them into "Elastic Block Store (gp3)" would read well and compare badly:
        `show` emits only JSON, so a caller filtering on either fact would have to parse a sentence.

        `type` is asserted ABSENT: it was the original name for the class, and it collided with the
        provider's own "volume type" on this very payload.
        """
        mock_handler_cls.return_value.show_volume.return_value = _DETAIL

        result = self.runner.invoke(volume_app, ["show", "--name", "home", "--json"])

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.output)
        self.assertEqual(payload["volume_class"], "ebs")
        self.assertEqual(payload["volume_type"], "gp3")
        self.assertNotIn("type", payload)


class TestVolumeStatus(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_status_accepts_a_missing_name(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        handler = mock_handler_cls.return_value
        handler.get_status.return_value = "attached"

        result = self.runner.invoke(volume_app, ["status"])

        self.assertEqual(result.exit_code, 0)
        handler.get_status.assert_called_once_with(None)
        self.assertIn("Volume status: attached", result.output)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_status_passes_an_explicit_name_through(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        handler = mock_handler_cls.return_value
        handler.get_status.return_value = "detached"

        result = self.runner.invoke(volume_app, ["status", "--name", "home/data"])

        self.assertEqual(result.exit_code, 0)
        handler.get_status.assert_called_once_with("home/data")
        self.assertIn("Volume status: detached", result.output)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_status_renders_exactly_one_line(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        """One volume, one status line -- like `jd host status` and `jd pool status`.

        Pinned as a count: the command used to report every volume at once, and a caller reading the
        status of "the" volume out of a multi-line listing would silently get the wrong one.
        """
        mock_handler_cls.return_value.get_status.return_value = "attached"

        result = self.runner.invoke(volume_app, ["status"])

        self.assertEqual(result.exit_code, 0)
        lines = [line for line in result.output.splitlines() if "status:" in line]
        self.assertEqual(len(lines), 1)


class TestVolumeHandlerErrorsPropagate(unittest.TestCase):
    """Every verb fails loudly when the handler does, following `test_host_app`.

    An untyped `Exception` on purpose: the typed refusals have their own tests, and what these pin is
    that nothing swallows a failure into a zero exit. A command that printed an empty table, or `Volume
    status: `, and exited 0 would be reported as success by any script and by CI.
    """

    def setUp(self) -> None:
        self.runner = CliRunner()

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_list_raises_when_the_handler_raises(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        mock_handler_cls.return_value.list_volumes.side_effect = Exception("Test error")

        result = self.runner.invoke(volume_app, ["list"])

        self.assertNotEqual(result.exit_code, 0)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_list_json_raises_when_the_handler_raises(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        """--json separately: it takes its own rendering path, so it can swallow what the table does not."""
        mock_handler_cls.return_value.list_volumes.side_effect = Exception("Test error")

        result = self.runner.invoke(volume_app, ["list", "--json"])

        self.assertNotEqual(result.exit_code, 0)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_show_raises_when_the_handler_raises(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        mock_handler_cls.return_value.show_volume.side_effect = Exception("Test error")

        result = self.runner.invoke(volume_app, ["show", "--name", "home"])

        self.assertNotEqual(result.exit_code, 0)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_status_raises_when_the_handler_raises(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        mock_handler_cls.return_value.get_status.side_effect = Exception("Test error")

        result = self.runner.invoke(volume_app, ["status"])

        self.assertNotEqual(result.exit_code, 0)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_backup_raises_when_the_handler_raises(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        mock_handler_cls.return_value.backup_volume.side_effect = Exception("Test error")

        result = self.runner.invoke(volume_app, ["backup", "--name", "home"])

        self.assertNotEqual(result.exit_code, 0)

    @patch("jupyter_deploy.cli.volume_app.cmd_utils.project_dir")
    @patch("jupyter_deploy.cli.volume_app.volume_handler.VolumeHandler")
    def test_backup_all_raises_when_the_handler_raises(self, mock_handler_cls: Mock, mock_project_dir: Mock) -> None:
        """--all is a distinct handler method, so it needs its own case.

        It is also the one where a swallowed failure is worst: `--all` is what a user runs before a
        restore, and a silent success there is what makes the restore recreate a volume EMPTY.
        """
        mock_handler_cls.return_value.backup_all.side_effect = Exception("Test error")

        result = self.runner.invoke(volume_app, ["backup", "--all"])

        self.assertNotEqual(result.exit_code, 0)
