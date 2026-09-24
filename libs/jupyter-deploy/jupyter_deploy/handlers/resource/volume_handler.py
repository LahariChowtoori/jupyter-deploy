import json
from datetime import datetime
from typing import Any

from jupyter_deploy import str_utils
from jupyter_deploy.engine.engine_outputs import EngineOutputsHandler
from jupyter_deploy.engine.enum import EngineType
from jupyter_deploy.engine.outdefs import StrTemplateOutputDefinition
from jupyter_deploy.engine.supervised_execution import DisplayManager
from jupyter_deploy.engine.terraform import tf_outputs, tf_variables
from jupyter_deploy.enum import ValueSource
from jupyter_deploy.exceptions import (
    BackupsNotReadyError,
    ConfigurationError,
    InvalidManifestError,
    ResourceNameRequiredError,
    VolumeNotBackupableError,
    VolumeNotFoundError,
)
from jupyter_deploy.handlers.base_project_handler import BaseProjectHandler
from jupyter_deploy.handlers.payloads import ResolvedVolume, VolumeBackupResult, VolumeDetail, VolumeInfo
from jupyter_deploy.handlers.resource.resource_utils import collect_results, resolve_node
from jupyter_deploy.manifest import (
    VOLUME_READINESS_COMMAND,
    VOLUME_SHOW_COMMAND,
    VOLUME_STATUS_COMMAND,
    JupyterDeployDynamicVolumeV1,
    JupyterDeployStaticVolumeV1,
)
from jupyter_deploy.provider import manifest_command_runner as cmd_runner
from jupyter_deploy.provider.resolved_clidefs import ResolvedCliParameter, StrResolvedCliParameter

# The one EBS snapshot state a volume can be created from. `pending` and `error` are the states that
# make an unusable snapshot look like the newest backup.
_BACKUP_STATE_COMPLETED = "completed"


class VolumeHandler(BaseProjectHandler):
    """Handler class to interact with the deployment's storage volumes and their backups."""

    _output_handler: EngineOutputsHandler

    def __init__(self, display_manager: DisplayManager) -> None:
        super().__init__(display_manager=display_manager)

        if self.engine == EngineType.TERRAFORM:
            self._output_handler = tf_outputs.TerraformOutputsHandler(
                project_path=self.project_path, project_manifest=self.project_manifest
            )
            self._variable_handler = tf_variables.TerraformVariablesHandler(
                project_path=self.project_path,
                project_manifest=self.project_manifest,
                display_manager=self.display_manager,
            )
        else:
            raise NotImplementedError(f"OutputsHandler implementation not found for engine: {self.engine}")

    # --- resolving declared values -----------------------------------------------------------------

    def _resolve_value(self, value_name: str) -> str:
        """Resolve a declared values: entry to its current value.

        Raises:
            ConfigurationError: If an output-backed value is absent, which means the project has not
                been applied yet -- volume ids only exist after an apply. Not a not-found: the value is
                declared and correct, it simply has nothing in it yet, and the remedy is a deploy.
        """
        value_def = self.project_manifest.get_declared_value(value_name)

        if value_def.get_source_type() == ValueSource.TEMPLATE_VARIABLE:
            variables = self._variable_handler.get_template_variables()
            variable = variables.get(value_def.source_key)
            value = None if variable is None else getattr(variable, "value", None)
            return "" if value is None else str(value)

        output_defs = self._output_handler.get_full_project_outputs()
        output_def = output_defs.get(value_def.source_key)
        if isinstance(output_def, StrTemplateOutputDefinition) and output_def.value:
            return output_def.value
        raise ConfigurationError(
            f"Volume information is not available: output '{value_def.source_key}' has no value yet.",
            hint="Deploy the project with 'jd up' first — a volume has no id until it exists.",
        )

    def _ids_named_by_backups_map(self, backups_map: str) -> set[str]:
        """Backup ids the template's backups-map variable currently names, across every volume.

        Read so that `jd volume backup` never deletes a snapshot the live configuration declares a volume
        was created from: that is deleting a dependency, not tidying. `snapshot_id` is ForceNew, so the
        next replacement of that volume would fail with `InvalidSnapshot.NotFound` having already
        destroyed it -- which happened in the E2E suite before its fixture learned to re-resolve the map.

        Best-effort by design: a map that cannot be read yields an empty set, so the supersede behaves as
        it did before rather than refusing to run.
        """
        if not backups_map:
            return set()
        try:
            value_def = self.project_manifest.get_declared_value(backups_map)
        except NotImplementedError:
            return set()
        if value_def.get_source_type() != ValueSource.TEMPLATE_VARIABLE:
            return set()

        variable = self._variable_handler.get_template_variables().get(value_def.source_key)
        value = None if variable is None else getattr(variable, "value", None)
        if not isinstance(value, dict):
            return set()
        return {str(backup_id) for backup_id in value.values() if backup_id}

    @staticmethod
    def _parse_json(raw: str) -> Any:
        try:
            return json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return None

    # --- inventory ---------------------------------------------------------------------------------

    def _resolve_static(self, declared: JupyterDeployStaticVolumeV1) -> ResolvedVolume:
        return ResolvedVolume(
            name=declared.name,
            volume_id=self._resolve_value(declared.volume_id_value),
            mount_point=declared.mount_point,
            description=declared.description,
            backups_map=declared.backups_map,
            volume_class=declared.type,
        )

    def _resolve_dynamic(self, declared: JupyterDeployDynamicVolumeV1) -> list[ResolvedVolume]:
        entries = self._parse_json(self._resolve_value(declared.inventory_value))
        if not isinstance(entries, list):
            return []

        def path_or_empty(entry: dict[str, Any], path: str) -> str:
            return str(resolve_node(entry, path) or "") if path else ""

        return [
            ResolvedVolume(
                name=path_or_empty(entry, declared.name_path),
                volume_id=path_or_empty(entry, declared.volume_id_path),
                mount_point=path_or_empty(entry, declared.mount_point_path),
                description=path_or_empty(entry, declared.description_path),
                backups_map=declared.backups_map,
                volume_class=declared.type,
            )
            for entry in entries
            if isinstance(entry, dict)
        ]

    def _inventory(self) -> list[ResolvedVolume]:
        """Every volume this deployment mounts, resolved from the manifest.

        No cloud call: the static set is declared outright and the dynamic set comes from a template
        output, the same way `jd component list` and `jd image list` read from the manifest.
        """
        volumes = self.project_manifest.get_volumes()
        resolved = [self._resolve_static(d) for d in volumes.static]
        for dynamic in volumes.dynamic:
            resolved.extend(self._resolve_dynamic(dynamic))
        return resolved

    def default_volume_name(self) -> str:
        """Return the identity of the volume the commands act on when no name is given.

        The FIRST entry of the manifest's `static:` list. A template declares its primary volume first,
        and that ordering is the contract -- so `jd volume show` with no --name means "the volume this
        deployment is fundamentally about" (`home` here) rather than "guess".

        Dynamic groups are deliberately excluded: their membership comes from configuration, so which
        member came "first" would change under the user as they add or remove mounts.

        Raises:
            ResourceNameRequiredError: If the template declares no static volume, in which case --name is
                required rather than defaulted to something arbitrary. The same error `jd image` raises
                for the same situation, so the CLI prints the list command for free.
        """
        static = self.project_manifest.get_volumes().static
        if not static:
            raise ResourceNameRequiredError("volume", "jd volume list")
        return static[0].name

    def _resolve_name(self, name: str | None) -> str:
        return name or self.default_volume_name()

    def _find(self, name: str) -> ResolvedVolume:
        """Return the volume `name` identifies, by identity or by provider id.

        Raises:
            VolumeNotFoundError: If nothing this deployment mounts answers to that name. Carries the
                identities that DO, since the overwhelmingly likely cause is a typo.
        """
        inventory = self._inventory()
        for volume in inventory:
            if name in (volume.name, volume.volume_id):
                return volume
        raise VolumeNotFoundError(name, [v.name or v.volume_id for v in inventory])

    # --- backups ----------------------------------------------------------------------------------

    def _run(self, cmd_name: str, **cli_params: str) -> dict[str, Any]:
        paramdefs: dict[str, ResolvedCliParameter[Any]] = {
            key: StrResolvedCliParameter(parameter_name=key, value=value) for key, value in cli_params.items()
        }
        command = self.project_manifest.get_command(cmd_name)
        runner = cmd_runner.ManifestCommandRunner(
            display_manager=self.display_manager,
            output_handler=self._output_handler,
            variable_handler=self._variable_handler,
        )
        runner.run_command_sequence(command, cli_paramdefs=paramdefs)
        return collect_results(runner, command)

    def _live_state(self, volumes: list[ResolvedVolume], command: str) -> dict[str, dict[str, Any]]:
        """Live provider state per volume id: zone, capacity, type, encryption, state.

        The manifest inventory says which volumes exist and what they are called; this says what they
        currently look like. Keyed by volume id, the one field both sides share.

        Grouped by storage CLASS, because classes answer to different APIs -- a block volume is found by
        tag, a file system has to be asked for by id. One command serves them all and branches on the class
        it is handed, so this is one call per class, not per volume.

        A template that does not declare the command gets no live state at all, and every volume is reported
        from its declaration alone. That is the whole opt-out; it is deliberately template-wide rather than
        per group, because the command already branches per class.
        """
        if not self.project_manifest.has_command(command):
            return {}

        by_class: dict[str, list[str]] = {}
        for volume in volumes:
            by_class.setdefault(volume.volume_class, []).append(volume.volume_id)

        live: dict[str, dict[str, Any]] = {}
        for volume_class, volume_ids in by_class.items():
            # Both are passed to every state command; its manifest declaration decides what to use. The
            # cli-param name stays `volume_type`: it is the manifest's own `source-key`, and the condition
            # that branches on it is written in the template, not here.
            results = self._run(command, volume_type=volume_class, volume_ids=",".join(volume_ids))
            described = results.get("volumes", [])
            if not isinstance(described, list):
                continue
            for entry in described:
                if isinstance(entry, dict) and entry.get("volume_id"):
                    live[str(entry["volume_id"])] = entry
        return live

    def _describe_backups(self) -> list[dict[str, Any]]:
        """Every backup of this deployment, for any volume.

        Not narrowed per volume: a deployment has at most one backup each across at most six volumes, so
        one call plus a client-side filter beats a call per volume.
        """
        results = self._run("volume.backups")
        backups = results.get("backups", [])
        return backups if isinstance(backups, list) else []

    @staticmethod
    def _latest_backup(
        backups: list[dict[str, Any]], volume_name: str, completed_only: bool = False
    ) -> dict[str, Any] | None:
        """The most recent backup for one volume identity.

        Normally zero or one candidate; sorting covers the transient window inside backup_volume(),
        between creating the replacement and deleting the one it supersedes.

        `completed_only` is for callers that intend to RESTORE from the result. A snapshot that is
        `pending` or `error` has the newest timestamp but cannot seed a volume, and restoring is
        destructive before it is validated, e.g. an `aws_ebs_volume` has no `create_before_destroy`,
        so the live volume is gone before `CreateVolume` rejects the snapshot, with the last good
        backup sitting right there. Callers that merely DISPLAY a backup pass False on purpose:
        an errored backup is exactly what a user needs to see.
        """
        if not volume_name:
            return None
        matching = [b for b in backups if b.get("volume_name") == volume_name]
        if completed_only:
            matching = [b for b in matching if str(b.get("state", "")) == _BACKUP_STATE_COMPLETED]
        if not matching:
            return None
        return sorted(matching, key=lambda b: str(b.get("created_at", "")), reverse=True)[0]

    # --- commands ---------------------------------------------------------------------------------

    def list_volumes(self) -> list[VolumeInfo]:
        """Return name / kind / description for every volume this deployment mounts.

        Shaped like `jd component list` rather than `jd pool list`: a volume has a declared `type` and
        `description`, and the type decides whether it can be backed up, so bare names would drop the
        one fact a reader most needs. No cloud call -- everything here comes from the manifest and the
        outputs it points at; live state is `show` and `status`.

        A referenced volume has no identity, so it is listed by its provider id.
        """
        return [
            VolumeInfo(
                name=volume.name or volume.volume_id,
                volume_class=volume.volume_class,
                description=volume.description,
            )
            for volume in self._inventory()
        ]

    def show_volume(self, name: str | None = None) -> VolumeDetail:
        """Return configuration detail for one volume, plus its backup if it has one.

        Defaults to the template's primary volume when no name is given.

        A volume whose kind has no backup mechanism is not asked about its backups at all -- both because
        the call would be pointless and because the provider reports what to say instead.
        """
        volume = self._find(self._resolve_name(name))
        live = self._live_state([volume], VOLUME_SHOW_COMMAND).get(volume.volume_id, {})
        backup = self._latest_backup(self._describe_backups(), volume.name) if volume.backup_eligible else None

        return VolumeDetail(
            name=volume.name or volume.volume_id,
            volume_id=volume.volume_id,
            mount_point=volume.mount_point,
            description=volume.description,
            zone=str(live.get("zone", "")),
            # None, not "": a volume with no reported size must not read as a volume of size zero.
            capacity=str(live["capacity"]) if live.get("capacity") is not None else None,
            # The class comes from the DECLARATION and the tier from the live call: the manifest is what
            # knows this is an `efs` mount, while only the provider knows it is `generalPurpose`.
            volume_class=volume.volume_class,
            volume_type=str(live.get("volume_type", "")),
            encrypted=bool(live.get("encrypted", False)),
            # The provider gets to answer this for a kind that can never have one (a file system says
            # "not needed"); otherwise it is the id of the backup, or "" for a volume awaiting its first.
            backup_id=str((backup or {}).get("backup_id") or live.get("backup_id", "")),
            # None rather than "" when there is no backup: these two describe a backup, so with no backup
            # to describe there is nothing to report, and the payload drops them.
            backup_state=str(backup["state"]) if backup and backup.get("state") else None,
            backup_timestamp=str(backup["created_at"]) if backup and backup.get("created_at") else None,
        )

    def get_status(self, name: str | None = None) -> str:
        """Return the status of ONE volume: a single word for the volume's own condition.

        Defaults to the template's primary volume when no name is given, matching `show` and `backup`.

        Deliberately just the volume's state -- `attached`, `detached`, `available` -- and not a summary
        that folds in its backup. `jd host status` and `jd pool status` each answer with one status of one
        thing, and a status that meant two things at once could not be compared, scripted against, or even
        read aloud without qualification. Backup freshness is a property of the BACKUP, and `show` reports
        it as `backup_state` / `backup_timestamp`.
        """
        volume = self._find(self._resolve_name(name))
        live = self._live_state([volume], VOLUME_STATUS_COMMAND).get(volume.volume_id, {})
        return str(live.get("state") or "unknown")

    def backup_volume(self, name: str | None = None) -> VolumeBackupResult:
        """Back up one volume, then delete the backup it supersedes.

        Defaults to the template's primary volume when no name is given.

        Order is load-bearing: the previous backup is deleted only AFTER the replacement reports
        `completed`, so a failure in between leaves the old backup intact rather than leaving the volume
        with no backup at all.
        """
        name = self._resolve_name(name)
        volume = self._find(name)

        # The two ineligibility causes get separate messages: `backup_eligible` folds them into one bool
        # for filtering, but telling a user "no backup mechanism exists" when the real problem is "you own
        # this volume" would send them looking for a flag that does not exist.
        if not volume.name:
            raise VolumeNotBackupableError(
                name,
                "this deployment references it rather than creating it, so it is never recreated and there "
                "is nothing to restore into",
                volume_class=volume.volume_class,
                hint="Back it up by the same means you created it with.",
            )
        if not volume.backups_map:
            kind = f"'{volume.volume_class}'" if volume.volume_class else "this class of"
            raise VolumeNotBackupableError(
                name,
                f"the template declares no backup mechanism for {kind} storage",
                volume_class=volume.volume_class,
                hint="Run 'jd volume backup --all' to back up every volume that supports it.",
            )

        superseded = [
            b.get("backup_id", "")
            for b in self._describe_backups()
            if b.get("backup_id") and b.get("volume_name") == volume.name
        ]

        results = self._run("volume.backup", volume_id=volume.volume_id, volume_name=volume.name)
        backup_id = str(results.get("backup_id", ""))

        # A snapshot the backups map still names is NOT deleted: the live configuration says a volume was
        # created from it, so removing it turns the next replacement of that volume into an
        # `InvalidSnapshot.NotFound` after the old volume is already gone. Costs at most one extra
        # snapshot per volume, and `jd config --restore-volumes` releases it by repointing the map.
        pinned = self._ids_named_by_backups_map(volume.backups_map)

        deleted = []
        for superseded_id in superseded:
            if superseded_id == backup_id:
                continue
            if superseded_id in pinned:
                self.display_manager.info(
                    f"Keeping backup {superseded_id}: the project's variables still declare a volume "
                    "was created from it."
                )
                continue
            self._run("volume.delete-backup", backup_id=superseded_id)
            deleted.append(superseded_id)

        return VolumeBackupResult(
            name=volume.name,
            volume_id=volume.volume_id,
            backup_id=backup_id,
            state=str(results.get("state", "")),
            superseded_backup_ids=deleted,
        )

    def backup_all(self) -> list[VolumeBackupResult]:
        """Back up every volume this deployment creates, skipping the ones it only references."""
        return [self.backup_volume(v.name) for v in self._inventory() if v.backup_eligible]

    def validate_backups_ready(self) -> None:
        """Raise unless every managed volume has a backup that provably contains ALL of its data.

        Not exposed as a CLI verb: this is a precondition of an operation that replaces volumes, and a
        user who ran it by hand and then wrote a file would hold a stale answer.

        Three checks, in the order that produces the most useful failure first:

          1. **Every eligible volume has a backup.** Delegated to `resolve_backup_ids`, which raises
             naming the volumes that have none -- restoring only some would recreate the rest empty.
          2. **The host is stopped.** The `validate-command` gates on this itself, so the refusal is the
             same `IncompatibleHostStateError` a backup would have raised.
          3. **The host has not run since the OLDEST of those backups.** This is the only check that
             proves anything about content. A backup's age says nothing -- an hour-old backup of a volume
             written to a minute ago is useless, and a month-old backup of an untouched volume is
             perfect. What matters is whether the volume could have changed since, and it could not if
             the instance has been stopped the whole time. The oldest backup is the bound because the
             weakest volume decides: one volume backed up before the last shutdown loses data even if
             every other volume was captured after it.

        An unknown stop time is a refusal, not a pass. EC2 does not always report one, and "cannot
        prove" must not read as "proved safe" for an operation that destroys the volumes.

        Raises:
            BackupsNotReadyError: If quiescence cannot be established for every volume, or if any
                managed volume has no backup at all.
            IncompatibleHostStateError: If the host is not stopped.
        """
        if not self.project_manifest.supports_volume_readiness():
            return

        # (1) raises, naming the volumes with no backup.
        _, backup_ids = self.resolve_backup_ids()

        backups = self._describe_backups()
        timestamps: dict[str, datetime] = {}
        undated: list[str] = []
        for volume_name, backup_id in backup_ids.items():
            created_at = next(
                (str(b.get("created_at", "")) for b in backups if b.get("backup_id") == backup_id),
                "",
            )
            parsed = str_utils.parse_timestamp(created_at)
            if parsed is None:
                undated.append(volume_name)
            else:
                timestamps[volume_name] = parsed

        if undated:
            raise BackupsNotReadyError(
                "the provider reported no creation time for the backup of "
                f"{', '.join(sorted(undated))}, so there is no way to tell whether it predates the "
                "last time the app was running",
                volume_names=sorted(undated),
                hint="Run 'jd volume backup --all' to take backups whose timing is known.",
            )

        # (2) and (3) are the command's, and it RAISES rather than reporting: it gates on the host being
        # stopped, then on every backup postdating the last shutdown. Nothing comes back because there is
        # nothing this method would do with a verdict that the refusal does not already say better.
        #
        # Every timestamp goes in at once, so the weakest volume decides -- one backup taken before the
        # last shutdown condemns the whole restore, since the volumes are replaced together.
        self._run(
            VOLUME_READINESS_COMMAND,
            backup_timestamps=",".join(instant.isoformat() for instant in timestamps.values()),
        )

    def resolve_backup_ids(self) -> tuple[str, dict[str, str]]:
        """Return (backups-map value name, {identity -> backup id}) for `jd config --restore-volumes`.

        Raises rather than partially restoring: a map missing a volume would silently recreate that
        volume EMPTY on the next apply, which is the data loss this whole path exists to prevent.

        Raises:
            BackupsNotReadyError: If no volume is eligible, or any eligible volume has no backup. Both
                are refusals of the SAME operation with a remedy attached, which is what this error
                carries and renders -- the reason alone would leave the user without a next step.
            InvalidManifestError: If the eligible volumes declare more than one backups-map. A template
                defect, not a user error: nothing the caller does changes it.
        """
        eligible = [v for v in self._inventory() if v.backup_eligible]
        if not eligible:
            raise BackupsNotReadyError(
                "this deployment creates no storage volumes, so there is nothing to restore",
                hint="Run 'jd volume list' to see what this deployment mounts.",
            )

        maps = {v.backups_map for v in eligible}
        if len(maps) != 1:
            raise InvalidManifestError(
                f"volumes declare more than one backups-map ({', '.join(sorted(maps))}); "
                "they cannot be restored in one step"
            )

        backups = self._describe_backups()
        resolved: dict[str, str] = {}
        missing: list[str] = []

        for volume in eligible:
            backup = self._latest_backup(backups, volume.name, completed_only=True)
            if backup is None or not backup.get("backup_id"):
                missing.append(volume.name)
            else:
                resolved[volume.name] = str(backup["backup_id"])

        if missing:
            raise BackupsNotReadyError(
                f"no backup exists for {', '.join(missing)}",
                volume_names=missing,
                hint="Run 'jd volume backup --all' first; restoring only some volumes would recreate the others empty.",
            )

        return next(iter(maps)), resolved
