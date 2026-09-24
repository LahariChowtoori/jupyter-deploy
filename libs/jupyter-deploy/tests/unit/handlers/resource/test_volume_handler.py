import unittest
from dataclasses import replace
from typing import Any
from unittest.mock import Mock, patch

from jupyter_deploy.exceptions import (
    BackupsNotReadyError,
    IncompatibleHostStateError,
    InvalidManifestError,
    ResourceNameRequiredError,
    VolumeNotBackupableError,
    VolumeNotFoundError,
)
from jupyter_deploy.handlers.payloads import ResolvedVolume
from jupyter_deploy.handlers.resource.volume_handler import VolumeHandler

_BACKUPS_MAP = "volume_backup_ids"

_HOME = ResolvedVolume(
    name="home",
    volume_id="vol-home",
    mount_point="/home/jovyan",
    description="JupyterLab home volume",
    backups_map=_BACKUPS_MAP,
)
_DATA = ResolvedVolume(
    name="home/data",
    volume_id="vol-data",
    mount_point="/home/jovyan/data",
    description="data",
    backups_map=_BACKUPS_MAP,
)
# Referenced by id: no identity, so no backups-map key can exist for it.
_REFERENCED = ResolvedVolume(
    name="",
    volume_id="vol-ref",
    mount_point="/home/jovyan/ref",
    description="ref",
    backups_map=_BACKUPS_MAP,
)
# An EFS mount: it HAS an identity, but its kind declares no backups map.
_SHARED_EFS = ResolvedVolume(
    name="home/shared",
    volume_id="fs-123",
    mount_point="/home/jovyan/shared",
    description="shared",
    backups_map="",
    volume_class="efs",
)

_HOME_BACKUP = {
    "backup_id": "snap-home",
    "volume_id": "vol-home",
    "state": "completed",
    "created_at": "2026-09-16T10:00:00+00:00",
    "volume_name": "home",
}

_LIVE = {
    "vol-home": {"volume_id": "vol-home", "zone": "us-west-2a", "capacity": "30Gi", "state": "attached"},
    "vol-data": {"volume_id": "vol-data", "zone": "us-west-2a", "capacity": "50Gi", "state": "attached"},
}


def _build_handler() -> VolumeHandler:
    """Build a VolumeHandler with its project/engine wiring stubbed out."""
    with patch.object(VolumeHandler, "__init__", lambda self, display_manager: None):
        handler = VolumeHandler(display_manager=Mock())  # type: ignore[call-arg]
    handler.display_manager = Mock()
    return handler


class TestBackupEligibility(unittest.TestCase):
    """Two independent reasons a volume is not backup-eligible; they must not be conflated."""

    def test_managed_volume_is_eligible(self) -> None:
        self.assertTrue(_HOME.backup_eligible)

    def test_referenced_volume_has_no_identity(self) -> None:
        self.assertFalse(_REFERENCED.backup_eligible)

    def test_kind_without_a_backups_map_is_not_eligible(self) -> None:
        self.assertFalse(_SHARED_EFS.backup_eligible)


class TestVolumeHandlerList(unittest.TestCase):
    def test_list_returns_name_kind_and_description(self) -> None:
        """Component-shaped rows, not bare names: the kind decides whether a backup is possible."""
        handler = _build_handler()
        with patch.object(handler, "_inventory", Mock(return_value=[_HOME, _SHARED_EFS])):
            volumes = handler.list_volumes()

        self.assertEqual([v.name for v in volumes], ["home", "home/shared"])
        self.assertEqual([v.volume_class for v in volumes], ["", "efs"])
        self.assertEqual(volumes[0].description, "JupyterLab home volume")

    def test_list_falls_back_to_volume_id_when_there_is_no_identity(self) -> None:
        handler = _build_handler()
        with patch.object(handler, "_inventory", Mock(return_value=[_REFERENCED])):
            self.assertEqual([v.name for v in handler.list_volumes()], ["vol-ref"])


class TestDefaultVolumeName(unittest.TestCase):
    """The default --name is the FIRST static entry, so the manifest's ordering is the contract."""

    @staticmethod
    def _handler_with_static(names: list[str]) -> VolumeHandler:
        handler = _build_handler()
        static = [Mock(name=f"static-{n}") for n in names]
        for mock_volume, volume_name in zip(static, names, strict=True):
            mock_volume.name = volume_name
        handler.project_manifest = Mock()  # type: ignore[assignment]
        handler.project_manifest.get_volumes.return_value = Mock(static=static, dynamic=[])
        return handler

    def test_first_static_entry_wins(self) -> None:
        handler = self._handler_with_static(["home", "scratch"])
        self.assertEqual(handler.default_volume_name(), "home")

    def test_no_static_volume_raises_rather_than_guessing(self) -> None:
        """The same error `jd image` raises, so the CLI prints the list command for free."""
        handler = self._handler_with_static([])
        with self.assertRaises(ResourceNameRequiredError) as ctx:
            handler.default_volume_name()

        self.assertEqual(ctx.exception.list_command, "jd volume list")

    def test_show_and_backup_fall_back_to_it(self) -> None:
        handler = _build_handler()
        with patch.object(handler, "default_volume_name", Mock(return_value="home")) as default:
            self.assertEqual(handler._resolve_name(None), "home")
            default.assert_called_once()

    def test_an_explicit_name_never_consults_the_default(self) -> None:
        handler = _build_handler()
        with patch.object(handler, "default_volume_name", Mock(side_effect=AssertionError)):
            self.assertEqual(handler._resolve_name("home/data"), "home/data")


class TestVolumeHandlerShow(unittest.TestCase):
    def test_show_joins_declaration_backup_and_live_state(self) -> None:
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            patch.object(handler, "_describe_backups", Mock(return_value=[_HOME_BACKUP])),
            patch.object(handler, "_live_state", Mock(return_value=_LIVE)),
        ):
            detail = handler.show_volume("home")

        self.assertEqual(detail.name, "home")
        self.assertEqual(detail.description, "JupyterLab home volume")
        self.assertEqual(detail.backup_id, "snap-home")
        self.assertEqual(detail.backup_state, "completed")
        self.assertEqual(detail.backup_timestamp, "2026-09-16T10:00:00+00:00")
        self.assertEqual(detail.capacity, "30Gi")
        self.assertEqual(detail.zone, "us-west-2a")

    def test_show_tolerates_missing_live_state(self) -> None:
        """A configured-but-unapplied volume has a declaration but no live state."""
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_DATA])),
            patch.object(handler, "_describe_backups", Mock(return_value=[])),
            patch.object(handler, "_live_state", Mock(return_value={})),
        ):
            detail = handler.show_volume("home/data")

        # None rather than "": an unreported size must not be serialized as a size of zero.
        self.assertIsNone(detail.capacity)
        self.assertNotIn("capacity", detail.to_dict())
        self.assertEqual(detail.backup_id, "")

    def test_show_omits_the_backup_fields_when_there_is_no_backup(self) -> None:
        """A volume awaiting its first backup reports no backup state and no timestamp.

        Dropped rather than emitted as "": a state of "" reads as a backup in an unknown condition, when
        the truth is that there is no backup to be in any condition. `backup_id` stays "" because that is
        the field a caller checks to find out whether one exists.
        """
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            patch.object(handler, "_describe_backups", Mock(return_value=[])),
            patch.object(handler, "_live_state", Mock(return_value=_LIVE)),
        ):
            detail = handler.show_volume("home")

        self.assertIsNone(detail.backup_state)
        self.assertIsNone(detail.backup_timestamp)
        self.assertNotIn("backup_state", detail.to_dict())
        self.assertNotIn("backup_timestamp", detail.to_dict())
        self.assertEqual(detail.to_dict()["backup_id"], "")

    def test_show_keeps_a_non_quantity_capacity_verbatim(self) -> None:
        """An elastic filesystem reports a word, not a number; the handler must not normalize it away."""
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_SHARED_EFS])),
            patch.object(handler, "_describe_backups", Mock(return_value=[])),
            patch.object(handler, "_live_state", Mock(return_value={"fs-123": {"capacity": "elastic"}})),
        ):
            detail = handler.show_volume("home/shared")

        self.assertEqual(detail.capacity, "elastic")

    def test_show_lets_the_provider_answer_for_a_kind_with_no_backups(self) -> None:
        """An EFS mount reports the provider's words for zone and backup_id, and no backup fields.

        The point is which side decides: the handler has no business knowing that a file system is
        regional, so it passes through whatever the provider reported rather than substituting a
        placeholder of its own.
        """
        handler = _build_handler()
        live = {"fs-123": {"zone": "regional", "backup_id": "not needed", "capacity": "elastic"}}
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_SHARED_EFS])),
            patch.object(handler, "_describe_backups", Mock(side_effect=AssertionError)),
            patch.object(handler, "_live_state", Mock(return_value=live)),
        ):
            detail = handler.show_volume("home/shared")

        self.assertEqual(detail.zone, "regional")
        self.assertEqual(detail.backup_id, "not needed")
        self.assertNotIn("backup_state", detail.to_dict())
        self.assertNotIn("backup_timestamp", detail.to_dict())

    def test_show_reports_the_declared_class_and_the_live_tier_separately(self) -> None:
        """The class comes from the manifest, `volume_type` from the provider -- two sources, two fields.

        Only the manifest knows this mount is `efs`; only the live call knows it is `generalPurpose`. A
        single merged label would have to be assembled here, which is also why it is not: the handler has
        no business inventing display strings for a payload whose only rendering is JSON.
        """
        handler = _build_handler()
        live = {"fs-123": {"volume_type": "generalPurpose", "capacity": "elastic"}}
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_SHARED_EFS])),
            patch.object(handler, "_live_state", Mock(return_value=live)),
        ):
            detail = handler.show_volume("home/shared")

        self.assertEqual(detail.volume_class, "efs")
        self.assertEqual(detail.volume_type, "generalPurpose")
        self.assertEqual(detail.to_dict()["volume_class"], "efs")

    def test_show_reports_the_class_even_when_the_provider_is_silent(self) -> None:
        """The class is declared, so an unapplied volume still knows what it is."""
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_SHARED_EFS])),
            patch.object(handler, "_live_state", Mock(return_value={})),
        ):
            detail = handler.show_volume("home/shared")

        self.assertEqual(detail.volume_class, "efs")
        self.assertEqual(detail.volume_type, "")

    def test_show_does_not_look_up_backups_for_an_ineligible_volume(self) -> None:
        """The backups call is skipped entirely: a kind with no backup mechanism has none to list.

        Enforced with a raising mock rather than a call-count assertion, so the test fails loudly at the
        point of the call instead of after the fact.
        """
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_SHARED_EFS])),
            patch.object(handler, "_describe_backups", Mock(side_effect=AssertionError)) as backups,
            patch.object(handler, "_live_state", Mock(return_value={})),
        ):
            handler.show_volume("home/shared")

        backups.assert_not_called()

    def test_show_unknown_name_raises(self) -> None:
        """A typo names what IS mounted: the overwhelmingly likely cause is a misspelling."""
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            self.assertRaises(VolumeNotFoundError) as ctx,
        ):
            handler.show_volume("nope")

        self.assertEqual(ctx.exception.valid_volumes, ["home"])


class TestVolumeHandlerStatus(unittest.TestCase):
    """One volume, one word. Not a summary, and not every volume at once."""

    def test_status_reports_the_live_state_of_one_volume(self) -> None:
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME, _DATA])),
            patch.object(handler, "_live_state", Mock(return_value=_LIVE)),
        ):
            self.assertEqual(handler.get_status("home"), "attached")

    def test_status_does_not_fold_in_the_backup(self) -> None:
        """The status of the volume, never the volume AND its backup.

        A status that meant two things at once could not be compared or scripted against, and the backups
        call it would need is not even made -- which is what this asserts.
        """
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            patch.object(handler, "_describe_backups", Mock(side_effect=AssertionError)),
            patch.object(handler, "_live_state", Mock(return_value=_LIVE)),
        ):
            status = handler.get_status("home")

        self.assertEqual(status, "attached")

    def test_status_defaults_to_the_primary_volume(self) -> None:
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME, _DATA])),
            patch.object(handler, "default_volume_name", Mock(return_value="home")),
            patch.object(handler, "_live_state", Mock(return_value=_LIVE)),
        ):
            self.assertEqual(handler.get_status(), "attached")

    def test_status_narrows_to_the_named_volume(self) -> None:
        """`--name` picks the volume, so a second volume's state must not leak into the answer."""
        handler = _build_handler()
        live = {**_LIVE, "vol-data": {"volume_id": "vol-data", "state": "detached"}}
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME, _DATA])),
            patch.object(handler, "_live_state", Mock(return_value=live)),
        ):
            self.assertEqual(handler.get_status("home/data"), "detached")

    def test_status_of_a_kind_that_needs_no_backup_is_just_its_state(self) -> None:
        """An EFS mount answers with its lifecycle state, with no backup caveat appended."""
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_SHARED_EFS])),
            patch.object(handler, "_live_state", Mock(return_value={"fs-123": {"state": "available"}})),
        ):
            self.assertEqual(handler.get_status("home/shared"), "available")

    def test_status_is_unknown_when_the_provider_reports_nothing(self) -> None:
        """An unapplied volume has no live state; "" would render as an empty status line."""
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            patch.object(handler, "_live_state", Mock(return_value={})),
        ):
            self.assertEqual(handler.get_status("home"), "unknown")

    def test_status_unknown_name_raises(self) -> None:
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            self.assertRaises(VolumeNotFoundError),
        ):
            handler.get_status("nope")


class TestVolumeHandlerBackup(unittest.TestCase):
    def test_backup_deletes_the_previous_one_only_after_the_new_one(self) -> None:
        """Order is the invariant: never delete before the replacement exists."""
        handler = _build_handler()
        calls: list[str] = []

        def fake_run(cmd_name: str, **_: str) -> dict[str, Any]:
            calls.append(cmd_name)
            if cmd_name == "volume.backup":
                return {"backup_id": "snap-new", "state": "completed"}
            return {}

        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            patch.object(handler, "_describe_backups", Mock(return_value=[_HOME_BACKUP])),
            patch.object(handler, "_ids_named_by_backups_map", Mock(return_value=set())),
            patch.object(handler, "_run", Mock(side_effect=fake_run)),
        ):
            result = handler.backup_volume("home")

        self.assertEqual(result.backup_id, "snap-new")
        self.assertEqual(result.superseded_backup_ids, ["snap-home"])
        self.assertLess(calls.index("volume.backup"), calls.index("volume.delete-backup"))

    def test_backup_does_not_delete_when_the_new_one_fails(self) -> None:
        handler = _build_handler()
        run: Mock = Mock(side_effect=RuntimeError("backup failed"))

        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            patch.object(handler, "_describe_backups", Mock(return_value=[_HOME_BACKUP])),
            patch.object(handler, "_run", run),
            self.assertRaises(RuntimeError),
        ):
            handler.backup_volume("home")

        self.assertNotIn("volume.delete-backup", [c.args[0] for c in run.call_args_list])

    def test_backup_refuses_a_referenced_volume(self) -> None:
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_REFERENCED])),
            self.assertRaises(VolumeNotBackupableError) as ctx,
        ):
            handler.backup_volume("vol-ref")

        self.assertIn("references it rather than creating it", ctx.exception.reason)

    def test_backup_refuses_a_kind_without_a_backups_map(self) -> None:
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_SHARED_EFS])),
            self.assertRaises(VolumeNotBackupableError) as ctx,
        ):
            handler.backup_volume("home/shared")

        self.assertIn("no backup mechanism", ctx.exception.reason)
        self.assertEqual(ctx.exception.volume_class, "efs")

    def test_the_two_refusals_do_not_share_a_message(self) -> None:
        """The whole point of the dedicated error: "you own this" must not read as "no mechanism exists"."""
        handler = _build_handler()
        reasons = []
        for inventory, name in ((_REFERENCED, "vol-ref"), (_SHARED_EFS, "home/shared")):
            with (
                patch.object(handler, "_inventory", Mock(return_value=[inventory])),
                self.assertRaises(VolumeNotBackupableError) as ctx,
            ):
                handler.backup_volume(name)
            reasons.append(ctx.exception.reason)
            self.assertIsNotNone(ctx.exception.hint)

        self.assertNotEqual(reasons[0], reasons[1])

    def test_backup_all_skips_ineligible_volumes(self) -> None:
        handler = _build_handler()
        backup_volume: Mock = Mock(return_value=Mock())

        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME, _REFERENCED, _SHARED_EFS])),
            patch.object(handler, "backup_volume", backup_volume),
        ):
            handler.backup_all()

        backup_volume.assert_called_once_with("home")


class TestResolveBackupIds(unittest.TestCase):
    def test_resolve_returns_the_map_and_its_value_name(self) -> None:
        handler = _build_handler()
        data_backup = {**_HOME_BACKUP, "backup_id": "snap-data", "volume_name": "home/data"}

        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME, _DATA])),
            patch.object(handler, "_describe_backups", Mock(return_value=[_HOME_BACKUP, data_backup])),
        ):
            value_name, resolved = handler.resolve_backup_ids()

        self.assertEqual(value_name, _BACKUPS_MAP)
        self.assertEqual(resolved, {"home": "snap-home", "home/data": "snap-data"})

    def test_resolve_raises_when_a_volume_has_no_backup(self) -> None:
        """A partial restore would recreate the unbacked volume EMPTY, so refuse the whole thing."""
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME, _DATA])),
            patch.object(handler, "_describe_backups", Mock(return_value=[_HOME_BACKUP])),
            self.assertRaises(BackupsNotReadyError) as ctx,
        ):
            handler.resolve_backup_ids()

        self.assertEqual(ctx.exception.volume_names, ["home/data"])
        # The remedy has to travel WITH the refusal: only the error decorator renders `hint`, so a
        # reason-only error tells the user they cannot proceed without telling them what to do.
        self.assertIn("jd volume backup --all", str(ctx.exception.hint))

    def test_resolve_ignores_ineligible_volumes(self) -> None:
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME, _REFERENCED, _SHARED_EFS])),
            patch.object(handler, "_describe_backups", Mock(return_value=[_HOME_BACKUP])),
        ):
            _, resolved = handler.resolve_backup_ids()

        self.assertEqual(resolved, {"home": "snap-home"})

    def test_resolve_raises_when_nothing_is_eligible(self) -> None:
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_SHARED_EFS])),
            self.assertRaises(BackupsNotReadyError),
        ):
            handler.resolve_backup_ids()

    def test_resolve_raises_on_conflicting_backups_maps(self) -> None:
        """A template defect, not a user error: nothing the caller does changes the answer."""
        handler = _build_handler()
        other = replace(_DATA, backups_map="other_backup_ids")
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME, other])),
            self.assertRaises(InvalidManifestError) as ctx,
        ):
            handler.resolve_backup_ids()

        self.assertIn("other_backup_ids", str(ctx.exception))


class TestLatestBackup(unittest.TestCase):
    def test_latest_wins_during_the_supersede_window(self) -> None:
        older = {**_HOME_BACKUP, "backup_id": "snap-old", "created_at": "2026-09-15T10:00:00+00:00"}
        newer = {**_HOME_BACKUP, "backup_id": "snap-new", "created_at": "2026-09-16T10:00:00+00:00"}
        result = VolumeHandler._latest_backup([older, newer], "home")
        assert result is not None
        self.assertEqual(result["backup_id"], "snap-new")

    def test_an_empty_identity_never_matches(self) -> None:
        """Guards a referenced volume against picking up a backup whose tag is also absent."""
        self.assertIsNone(VolumeHandler._latest_backup([{**_HOME_BACKUP, "volume_name": ""}], ""))


class TestLiveStateGrouping(unittest.TestCase):
    """Live state is resolved per class: one shared command, which branches on the class it is handed.

    The cli-param it is handed is still called `volume_type`: that name is the manifest's own `source-key`,
    and the condition that branches on it is written in the template, not here.
    """

    @staticmethod
    def _handler_with_state_command(declared: bool = True) -> VolumeHandler:
        handler = _build_handler()
        handler.project_manifest = Mock()  # type: ignore[assignment]
        handler.project_manifest.has_command.return_value = declared
        return handler

    def test_one_call_per_class_not_per_volume(self) -> None:
        handler = self._handler_with_state_command()
        efs = ResolvedVolume(
            name="home/shared",
            volume_id="fs-123",
            mount_point="/home/jovyan/shared",
            description="shared",
            backups_map="",
            volume_class="efs",
        )
        ebs_a = ResolvedVolume(**{**vars(_HOME), "volume_class": "ebs"})
        ebs_b = ResolvedVolume(**{**vars(_DATA), "volume_class": "ebs"})

        def fake_run(cmd_name: str, **kwargs: str) -> dict[str, Any]:
            if kwargs.get("volume_type") == "efs":
                return {"volumes": [{"volume_id": "fs-123", "state": "available", "capacity": "elastic"}]}
            return {"volumes": [{"volume_id": "vol-home", "state": "attached"}]}

        run: Mock = Mock(side_effect=fake_run)
        with patch.object(handler, "_run", run):
            live = handler._live_state([ebs_a, ebs_b, efs], "volume.status")

        # Two EBS volumes share a kind, so the command runs twice in total, not three times.
        self.assertEqual([c.args[0] for c in run.call_args_list], ["volume.status", "volume.status"])
        self.assertEqual(
            sorted(c.kwargs["volume_type"] for c in run.call_args_list),
            ["ebs", "efs"],
        )
        self.assertEqual(live["fs-123"]["capacity"], "elastic")
        self.assertEqual(live["vol-home"]["state"], "attached")

    def test_efs_ids_are_passed_to_the_command(self) -> None:
        """EFS has no tag filter, so the ids must reach the command alongside the type."""
        handler = self._handler_with_state_command()
        efs = ResolvedVolume(**{**vars(_SHARED_EFS), "volume_class": "efs"})
        run: Mock = Mock(return_value={"volumes": []})

        with patch.object(handler, "_run", run):
            handler._live_state([efs], "volume.status")

        self.assertEqual(run.call_args.kwargs["volume_ids"], "fs-123")
        self.assertEqual(run.call_args.kwargs["volume_type"], "efs")

    def test_a_template_that_does_not_declare_the_command_triggers_no_call(self) -> None:
        """Opting out is template-wide now, not per group: every volume falls back to its declaration.

        Per-group was possible while each entry named its own command, and bought nothing -- the single
        command already branches on the storage class it is handed, so a group that needed different
        handling needed a new branch, not a new command name.
        """
        handler = self._handler_with_state_command(declared=False)
        run: Mock = Mock(side_effect=AssertionError)

        with patch.object(handler, "_run", run):
            self.assertEqual(handler._live_state([_HOME], "volume.status"), {})

        run.assert_not_called()

        run.assert_not_called()


_STOPPED_AT = "2026-09-17T14:00:00+00:00"
_AFTER_STOP = "2026-09-17T14:05:00+00:00"
_BEFORE_STOP = "2026-09-17T13:00:00+00:00"


def _guarded_handler() -> VolumeHandler:
    """A handler whose template declares a readiness command."""
    handler = _build_handler()
    handler.project_manifest = Mock()  # type: ignore[assignment]
    handler.project_manifest.get_volumes.return_value = Mock(static=[], dynamic=[])
    handler.project_manifest.supports_volume_readiness.return_value = True
    return handler


class TestValidateBackupsReady(unittest.TestCase):
    """The handler's share of the readiness check: make sure a backup exists, and hand over its instant.

    The judgement itself belongs to the readiness command, which gates on the host and RAISES — so what
    is worth testing here is that every backup's timestamp reaches it, and that the two cases only the
    handler can see (no backup at all, a backup with no usable timestamp) refuse before it is called.
    """

    def test_forwards_every_backup_timestamp_to_the_readiness_command(self) -> None:
        """All of them, in one call: the weakest backup has to be able to decide.

        Forwarding only the newest would pass a set where an older volume's capture predates the last
        shutdown — the exact case that loses data, since the volumes are replaced together.
        """
        handler = _guarded_handler()
        backup_ids = {"home": "snap-new", "home/data": "snap-old"}
        backups = [
            {"backup_id": "snap-new", "created_at": _AFTER_STOP},
            {"backup_id": "snap-old", "created_at": _BEFORE_STOP},
        ]
        run: Mock = Mock(return_value={})
        with (
            patch.object(handler, "resolve_backup_ids", Mock(return_value=("map", backup_ids))),
            patch.object(handler, "_describe_backups", Mock(return_value=backups)),
            patch.object(handler, "_run", run),
        ):
            handler.validate_backups_ready()

        self.assertEqual(run.call_args.args[0], "volume.validate-backups-ready")
        forwarded = run.call_args.kwargs["backup_timestamps"].split(",")
        self.assertEqual(sorted(forwarded), sorted([_AFTER_STOP, _BEFORE_STOP]))

    def test_a_refusal_from_the_readiness_command_propagates(self) -> None:
        """The command owns the verdict, so the handler must not swallow or reinterpret it."""
        handler = _guarded_handler()
        with (
            patch.object(handler, "resolve_backup_ids", Mock(return_value=("map", {"home": "snap-1"}))),
            patch.object(
                handler,
                "_describe_backups",
                Mock(return_value=[{"backup_id": "snap-1", "created_at": _BEFORE_STOP}]),
            ),
            patch.object(handler, "_run", Mock(side_effect=IncompatibleHostStateError("ran since"))),
            self.assertRaises(IncompatibleHostStateError),
        ):
            handler.validate_backups_ready()

    def test_an_undated_backup_is_refused_before_the_command_runs(self) -> None:
        """A backup whose creation time the provider never reported cannot be placed either side.

        Refused here rather than forwarded, because the command drops entries it cannot parse — passing
        an undated backup along would make the check pass by omitting the very thing in question.
        """
        handler = _guarded_handler()
        with (
            patch.object(handler, "resolve_backup_ids", Mock(return_value=("map", {"home": "snap-1"}))),
            patch.object(handler, "_describe_backups", Mock(return_value=[{"backup_id": "snap-1"}])),
            patch.object(handler, "_run", Mock(side_effect=AssertionError)),
            self.assertRaises(BackupsNotReadyError) as ctx,
        ):
            handler.validate_backups_ready()

        self.assertEqual(ctx.exception.volume_names, ["home"])

    def test_a_volume_with_no_backup_fails_before_any_host_call(self) -> None:
        """Missing backups are `resolve_backup_ids`' refusal, and it comes first.

        Ordered deliberately: "you have no backup of X" is more useful than "your host is running", and a
        user who is told to stop the host first would then be told to take a backup anyway.
        """
        handler = _guarded_handler()
        missing = BackupsNotReadyError("no backup exists for home", volume_names=["home"])
        with (
            patch.object(handler, "resolve_backup_ids", Mock(side_effect=missing)),
            patch.object(handler, "_run", Mock(side_effect=AssertionError)),
            self.assertRaises(BackupsNotReadyError),
        ):
            handler.validate_backups_ready()

    def test_a_template_that_does_not_declare_the_command_checks_nothing(self) -> None:
        """A template whose storage is always safe to restore must not pay for this, or be refused by it."""
        handler = _build_handler()
        handler.project_manifest = Mock()  # type: ignore[assignment]
        handler.project_manifest.supports_volume_readiness.return_value = False
        with (
            patch.object(handler, "resolve_backup_ids", Mock(side_effect=AssertionError)),
            patch.object(handler, "_run", Mock(side_effect=AssertionError)),
        ):
            handler.validate_backups_ready()  # does not raise


class TestRestoreSourceMustBeUsable(unittest.TestCase):
    """A snapshot that cannot seed a volume must never be resolvable as a restore source."""

    _ERRORED_NEWER = {
        "backup_id": "snap-errored",
        "volume_id": "vol-home",
        "state": "error",
        "created_at": "2026-09-17T10:00:00+00:00",
        "volume_name": "home",
    }
    _PENDING_NEWER = {
        "backup_id": "snap-pending",
        "volume_id": "vol-home",
        "state": "pending",
        "created_at": "2026-09-17T11:00:00+00:00",
        "volume_name": "home",
    }

    def test_resolve_skips_an_errored_snapshot_for_the_older_good_one(self) -> None:
        """The newest is not the answer: restoring from `error` destroys the volume, then fails."""
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            patch.object(handler, "_describe_backups", Mock(return_value=[_HOME_BACKUP, self._ERRORED_NEWER])),
        ):
            _, resolved = handler.resolve_backup_ids()

        self.assertEqual(resolved, {"home": "snap-home"})

    def test_resolve_skips_a_pending_snapshot(self) -> None:
        """Same shape via the wait timing out: `pending` is the newest and cannot be restored from."""
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            patch.object(handler, "_describe_backups", Mock(return_value=[_HOME_BACKUP, self._PENDING_NEWER])),
        ):
            _, resolved = handler.resolve_backup_ids()

        self.assertEqual(resolved, {"home": "snap-home"})

    def test_resolve_refuses_when_only_an_unusable_snapshot_exists(self) -> None:
        """Better to refuse than to resolve an id the apply cannot use."""
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            patch.object(handler, "_describe_backups", Mock(return_value=[self._ERRORED_NEWER])),
            self.assertRaises(BackupsNotReadyError),
        ):
            handler.resolve_backup_ids()

    def test_show_still_reports_the_errored_backup(self) -> None:
        """`show` must NOT hide it: an errored backup is what the user needs to see."""
        handler = _build_handler()
        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            patch.object(handler, "_describe_backups", Mock(return_value=[_HOME_BACKUP, self._ERRORED_NEWER])),
            patch.object(handler, "_live_state", Mock(return_value={})),
        ):
            detail = handler.show_volume("home")

        self.assertEqual(detail.backup_id, "snap-errored")
        self.assertEqual(detail.backup_state, "error")


class TestSupersedeRespectsTheBackupsMap(unittest.TestCase):
    """Deleting a snapshot the live configuration was built from is deleting a dependency."""

    @staticmethod
    def _handler_with_map(pinned: set[str]) -> VolumeHandler:
        handler = _build_handler()
        handler._ids_named_by_backups_map = Mock(return_value=pinned)  # type: ignore[method-assign]
        return handler

    def _run_backup(self, handler: VolumeHandler) -> Any:
        def fake_run(cmd_name: str, **_: str) -> dict[str, Any]:
            if cmd_name == "volume.backup":
                return {"backup_id": "snap-new", "state": "completed"}
            return {}

        with (
            patch.object(handler, "_inventory", Mock(return_value=[_HOME])),
            patch.object(handler, "_describe_backups", Mock(return_value=[_HOME_BACKUP])),
            patch.object(handler, "_run", Mock(side_effect=fake_run)) as run,
        ):
            result = handler.backup_volume("home")
        return result, run

    def test_a_pinned_snapshot_is_not_deleted(self) -> None:
        """`ebs_snapshot_ids` still names snap-home, so the next replacement needs it to exist."""
        handler = self._handler_with_map({"snap-home"})

        result, run = self._run_backup(handler)

        self.assertEqual(result.superseded_backup_ids, [])
        self.assertNotIn("volume.delete-backup", [c.args[0] for c in run.call_args_list])

    def test_an_unpinned_snapshot_is_still_deleted(self) -> None:
        handler = self._handler_with_map({"snap-something-else"})

        result, run = self._run_backup(handler)

        self.assertEqual(result.superseded_backup_ids, ["snap-home"])
        self.assertIn("volume.delete-backup", [c.args[0] for c in run.call_args_list])
