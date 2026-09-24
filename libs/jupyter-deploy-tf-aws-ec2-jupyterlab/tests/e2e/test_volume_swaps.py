"""Apply #3 of the mutating pass: an availability-zone swap that preserves every volume's data.

This is the file the whole `jd volume` command group exists for. An EBS volume cannot cross zones, so
changing `availability_zone` on a live deployment REPLACES every managed volume — and without a
backup, replacing it means losing the user's notebooks. The sequence under test is the one a user
actually runs when the zone they are in has no capacity for the instance they want:

    jd host stop
    jd volume backup --all
    jd config --restore-volumes --availability-zone <other-zone>
    jd up

Three properties, in order of how badly they fail:

  1. **`--restore-volumes` refuses backups that cannot be shown to hold the data.** Resolving a backup
     into `ebs_snapshot_ids` REPLACES the volume on the next apply, and it succeeds silently when the
     backup is old — terraform rebuilds from it, reports success, and the work since is gone. So the CLI
     checks before writing: every volume has a backup, the host is stopped, and each backup postdates the
     last shutdown.

     There is deliberately NO check on the no-flag path. The engine precondition that used to guard it was
     removed: it could only see whether `ebs_snapshot_ids` had a key, so it stopped firing once the map was
     first populated, and reading the live volume's zone needed a data source that is unknown at plan time
     on a fresh deploy — which broke every from-scratch deploy. Changing the zone without asking for a
     restore is an accepted footgun.
  2. **The safe version restores.** A marker file uploaded before the swap is still readable after it,
     on the home volume AND on the additional EBS volume — proving each was recreated from its own
     backup rather than empty.
  3. **EFS needs none of this.** A file system is regional, so the swap only relocates its mount
     target. Its marker survives with no backup at all, which is also why `backup --name <efs>` is
     refused rather than silently doing nothing.

Runnable in any order, unlike the other two mutating files: ``volumes_provisioned`` provisions the
mounts and pins the deployment to the BASE zone, so the refusal test has a real zone change to refuse
no matter what ran before it. It is still ordered last, because it leaves the deployment in a
different zone and because a zone swap is the most expensive thing here.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_jupyter_deploy.cli import JDCliError
from pytest_jupyter_deploy.deployment import EndToEndDeployment
from pytest_jupyter_deploy.files import upload_file_on_server
from pytest_jupyter_deploy.plugin import skip_if_testvars_not_set
from pytest_jupyter_deploy.volumes import get_volume_details, get_volume_names

from .constants import (
    EBS_MOUNT,
    EBS_VOLUME,
    EFS_MOUNT,
    EFS_VOLUME,
    HOME_VOLUME,
    ORDER_MUTATING_VOLUME_SWAPS,
)

_APPLY_TIMEOUT_SECONDS = 3600
_BACKUP_TIMEOUT_SECONDS = 1800

# Uploaded before the swap, read after it. The CONTENT is the assertion, not the file's existence: an
# empty recreated volume with a same-named file would pass an existence check.
_MARKER_SOURCE = Path(__file__).parent / "files" / "volume_marker.txt"
_MARKER_CONTENT = "volume-marker-written-before-the-zone-swap"
_HOME_MARKER = "e2e_swap_home.txt"
_EBS_MARKER = "external-ebs1/e2e_swap_ebs.txt"
_EFS_MARKER = "external-efs1/e2e_swap_efs.txt"


def _zone_change_pending(e2e_deployment: EndToEndDeployment, target_zone: str) -> bool:
    """True when the deployment is applied and its home volume sits in a zone other than `target_zone`.

    A failure to read the volume means the project is not applied yet, which is not a pending change: a
    fresh deploy lands in the target zone directly, with no volume to replace and no backup to restore.
    """
    try:
        live_zone = get_volume_details(e2e_deployment, HOME_VOLUME)["zone"]
    except JDCliError:
        return False
    return bool(live_zone) and live_zone != target_zone


@pytest.fixture(scope="module")
def volumes_provisioned(e2e_deployment: EndToEndDeployment, base_availability_zone: str) -> Iterator[None]:
    """Put the deployment in the state this module assumes: both mounts present, in the BASE zone.

    This is what makes the file a valid entry point on its own, in any order. Pinning the zone
    explicitly matters as much as provisioning the mounts: the refusal test needs the deployment to be
    somewhere other than JD_E2E_ALT_AVAILABILITY_ZONE, and if a previous run already swapped it there,
    a test that merely *checked* that would skip its own assertion rather than set up for it.

    The mount flags must be passed even when they are already set: they are LIST variables, and a
    `jd config` that omits them would unmount — and potentially destroy — the very volumes these tests
    are about to assert on.

    `--restore-volumes` is added ONLY when the deployment has to move zones, and both halves of that are
    load-bearing:

      - **Needed when it does move.** A zone change replaces every block volume, and it restores each one
        from whatever `ebs_snapshot_ids` names. On a re-run that map is left over from the previous swap
        and may name a snapshot that `jd volume backup` has since superseded and DELETED — `test_volume.py`
        alone does that twice to the home volume. The apply then dies with `InvalidSnapshot.NotFound`
        halfway through, having already destroyed the volume it could not recreate. Re-resolving points
        the map at the backups that exist now, which is why fresh ones are taken first.
      - **Harmful when it does not.** `snapshot_id` forces replacement on an `aws_ebs_volume`, so passing
        this flag with no zone change pending would replace healthy volumes with the contents of their
        last backup, discarding everything written since. It cannot be passed unconditionally.
    """
    flags = [
        "--availability-zone",
        base_availability_zone,
        "--additional-ebs-mounts",
        EBS_MOUNT,
        "--additional-efs-mounts",
        EFS_MOUNT,
    ]
    if _zone_change_pending(e2e_deployment, base_availability_zone):
        # The full recipe, because `--restore-volumes` now validates: it refuses unless the host is
        # stopped and the backups postdate the last shutdown. Taking them here rather than reusing
        # whatever exists is the point -- a leftover backup from a previous run is exactly what the
        # validation rejects, and rightly so.
        e2e_deployment.ensure_server_running()
        e2e_deployment.cli.run_command(["jupyter-deploy", "host", "stop"])
        e2e_deployment.cli.run_command(
            ["jupyter-deploy", "volume", "backup", "--all"],
            timeout_seconds=_BACKUP_TIMEOUT_SECONDS,
        )
        flags.append("--restore-volumes")

    e2e_deployment.ensure_deployed_with(flags, timeout_seconds=_APPLY_TIMEOUT_SECONDS)
    e2e_deployment.ensure_server_running(wait_after_restart=True)
    yield


@pytest.fixture(scope="module")
def markers_uploaded(e2e_deployment: EndToEndDeployment, volumes_provisioned: None) -> Iterator[None]:
    """Upload the marker file onto all three mounts, while the host is still running.

    Separate from the host-stopping fixture because the two need OPPOSITE host states and cannot be
    reordered: uploading needs a live host, and `jd volume backup` refuses unless the instance is
    stopped. Uploaded rather than written with a shell redirect — the quoting in
    `server exec -- sh -c 'printf ... > file'` does not survive the SSM layer, whereas
    `upload_file_on_server` base64-encodes the payload.
    """
    for path in (_HOME_MARKER, _EBS_MARKER, _EFS_MARKER):
        upload_file_on_server(e2e_deployment, _MARKER_SOURCE, path)
    yield


@pytest.fixture(scope="module")
def quiesced_with_markers(markers_uploaded: None, stopped_host: None) -> Iterator[None]:
    """Markers on disk, host stopped — the ordering, expressed once.

    The fixture order in this signature is the whole content of this fixture. `stopped_host` is
    module-scoped and generic (it lives in the plugin), so whichever test requests it first stops the
    host for the rest of the module. Relying on each test's parameter order to get "upload, then stop"
    right would break the moment a test listed them the other way, or requested only the stop.
    """
    yield


@pytest.mark.order(ORDER_MUTATING_VOLUME_SWAPS)
@pytest.mark.mutating
def test_discovery_lists_every_mounted_volume(
    e2e_deployment: EndToEndDeployment,
    volumes_provisioned: None,
) -> None:
    """`jd volume list` reports all three mounts, by the path the user sees in the app, with classes.

    Identity is the mount path (`home/external-ebs1`), not the terraform resource name, not the
    `name=` field of the mount spec, and not the volume id. That choice is what lets a user go from
    "the folder in my file browser" to a `--name` without a lookup table, so it is asserted rather
    than assumed.

    The only listing in the suite that spans two storage classes, so it is the only place the `Class`
    column can be shown to actually discriminate — and it is the column that tells a reader which of
    these three volumes `backup --all` will skip.
    """
    result = e2e_deployment.cli.run_command(["jupyter-deploy", "volume", "list", "--json"])
    classes = {volume["name"]: volume["volume_class"] for volume in json.loads(result.stdout)}

    for expected in (HOME_VOLUME, EBS_VOLUME, EFS_VOLUME):
        assert expected in classes, f"Expected '{expected}' among the listed volumes, got {list(classes)}"

    assert classes[HOME_VOLUME] == "ebs"
    assert classes[EBS_VOLUME] == "ebs"
    assert classes[EFS_VOLUME] == "efs", (
        f"The EFS mount must be distinguishable from the block volumes, got {classes[EFS_VOLUME]!r}"
    )


@pytest.mark.order(ORDER_MUTATING_VOLUME_SWAPS + 1)
@pytest.mark.mutating
def test_show_and_status_describe_the_additional_ebs_volume(
    e2e_deployment: EndToEndDeployment,
    volumes_provisioned: None,
) -> None:
    """The additional EBS volume reports its own provisioned size and zone, not the home volume's.

    The size check is what proves the inventory is per-volume: this one was requested at 50 GiB while
    the home volume is 30, so a `show` that resolved every identity to the same live record would
    pass an "is it non-empty" check and fail this one.
    """
    result = e2e_deployment.cli.run_command(["jupyter-deploy", "volume", "show", "--name", EBS_VOLUME, "--json"])
    detail = json.loads(result.stdout)

    assert detail["name"] == EBS_VOLUME
    assert detail["volume_id"].startswith("vol-"), f"Expected an EBS volume id, got {detail['volume_id']}"
    assert detail["capacity"] == "50Gi", f"Expected the requested 50 GiB, got {detail['capacity']!r}"
    assert detail["zone"].startswith("us-"), f"A block volume is zonal, so it must report a zone: {detail['zone']!r}"
    assert detail["mount_point"] == "/home/jovyan/external-ebs1"

    status = e2e_deployment.cli.run_command(["jupyter-deploy", "volume", "status", "--name", EBS_VOLUME])
    assert "Volume status: attached" in e2e_deployment.cli.strip_ansi(status.stdout)


@pytest.mark.order(ORDER_MUTATING_VOLUME_SWAPS + 2)
@pytest.mark.mutating
def test_show_and_status_describe_the_efs_volume(
    e2e_deployment: EndToEndDeployment,
    volumes_provisioned: None,
) -> None:
    """The EFS mount answers in words where a block volume answers with values.

    `elastic` capacity, `regional` zone, `not needed` backup: three angles on one fact, that a regional
    file system provisions no size, sits in no single zone, and therefore cannot be lost to a zone
    change. Each is a WORD rather than an empty string on purpose — "" reads as "not reported yet", a
    state a user might wait out or try to fix, when none of these will ever have a value.

    The `backup_state` and `backup_timestamp` keys are asserted ABSENT: they describe a backup, and
    there is no backup here to be in any state. Their absence is also what proves the handler never
    went looking for one.
    """
    result = e2e_deployment.cli.run_command(["jupyter-deploy", "volume", "show", "--name", EFS_VOLUME, "--json"])
    detail = json.loads(result.stdout)

    assert detail["name"] == EFS_VOLUME
    assert detail["volume_id"].startswith("fs-"), f"Expected an EFS file system id, got {detail['volume_id']}"
    assert detail["capacity"] == "elastic", f"Expected 'elastic', got {detail['capacity']!r}"
    assert detail["zone"] == "regional", f"Expected 'regional', got {detail['zone']!r}"
    assert detail["backup_id"] == "not needed", f"Expected 'not needed', got {detail['backup_id']!r}"

    # The one volume in the suite where the two fields visibly disagree, which is the point of having both:
    # `generalPurpose` is an EFS performance mode and would be meaningless without the class beside it.
    assert detail["volume_class"] == "efs", f"Unexpected storage class: {detail['volume_class']!r}"
    assert detail["volume_type"] == "generalPurpose", f"Unexpected performance mode: {detail['volume_type']!r}"
    for absent in ("backup_state", "backup_timestamp"):
        assert absent not in detail, f"{absent} describes a backup, and this volume has none: {detail}"

    # The status is the file system's own lifecycle state — no backup caveat appended, because a status
    # describes one thing.
    status = e2e_deployment.cli.run_command(["jupyter-deploy", "volume", "status", "--name", EFS_VOLUME])
    assert "Volume status: available" in e2e_deployment.cli.strip_ansi(status.stdout)


@pytest.mark.order(ORDER_MUTATING_VOLUME_SWAPS + 3)
@pytest.mark.mutating
def test_backup_refuses_the_efs_volume(
    e2e_deployment: EndToEndDeployment,
    volumes_provisioned: None,
) -> None:
    """`jd volume backup --name home/external-efs1` fails cleanly, and says why.

    The template declares no backups-map for EFS, which is how "this kind has no backup mechanism"
    is expressed. The refusal must not read like the other refusal (a volume the deployment only
    references), because the two send a user to completely different places.

    Ordered before the stopped-host tests, and independent of host state: the handler refuses on the
    declaration alone, without reaching the provider.
    """
    with pytest.raises(JDCliError) as excinfo:
        e2e_deployment.cli.run_command(["jupyter-deploy", "volume", "backup", "--name", EFS_VOLUME])

    message = " ".join(str(excinfo.value).split())
    assert "cannot be backed up" in message, f"Unexpected failure reason: {message}"
    assert "no backup mechanism" in message, f"Expected the kind-has-no-mechanism reason, got: {message}"
    assert "Traceback (most recent call last)" not in message, f"The CLI leaked a stack trace:\n{message}"


@pytest.mark.order(ORDER_MUTATING_VOLUME_SWAPS + 4)
@pytest.mark.mutating
def test_backup_the_additional_ebs_volume(
    e2e_deployment: EndToEndDeployment,
    quiesced_with_markers: None,
) -> None:
    """`jd volume backup --name home/external-ebs1` backs up that volume and only that volume.

    The home volume's backup must be untouched: `--name` is how a user backs up one volume before a
    risky change, and a command that quietly re-backed-up everything would be both slow and
    surprising.

    First test to request `quiesced_with_markers`, so it is where the markers are uploaded and the host
    is stopped. Everything after it runs against a stopped host, which is fine — the remaining
    commands are control-plane only.
    """
    home_backup_before = get_volume_details(e2e_deployment, HOME_VOLUME)["backup_id"]

    e2e_deployment.cli.run_command(
        ["jupyter-deploy", "volume", "backup", "--name", EBS_VOLUME],
        timeout_seconds=_BACKUP_TIMEOUT_SECONDS,
    )

    detail = get_volume_details(e2e_deployment, EBS_VOLUME)
    assert detail["backup_id"].startswith("snap-"), f"Expected a snapshot id, got {detail['backup_id']!r}"
    assert detail["backup_state"] == "completed"

    assert get_volume_details(e2e_deployment, HOME_VOLUME)["backup_id"] == home_backup_before, (
        "Backing up one volume by name must not touch another volume's backup"
    )


@pytest.mark.order(ORDER_MUTATING_VOLUME_SWAPS + 5)
@pytest.mark.mutating
@skip_if_testvars_not_set(["JD_E2E_ALT_AVAILABILITY_ZONE"])
def test_a_backup_older_than_the_last_session_is_refused(
    e2e_deployment: EndToEndDeployment,
    quiesced_with_markers: None,
    base_availability_zone: str,
    alt_availability_zone: str,
) -> None:
    """A swap is refused when the app ran AFTER the backups were taken.

    The silent-data-loss case, and the only one with no symptom: terraform recreates the volume from
    that older snapshot, reports success, and the app comes back missing a session's work. Nothing
    fails, so nothing is noticed — which is why the refusal has to happen here, before the plan.

    Backup age is deliberately NOT what decides. A ten-minute-old backup of a volume written to five
    minutes ago is useless, and a month-old backup of a volume nothing has touched is perfect. The test
    is whether the volume could have changed since, and it could not while the host was stopped — so
    the check compares each backup against the host's LAST stop, which is what this test manufactures:
    back up, run the app, stop it again.

    The refusal comes from the provider gate (`verify-instance-stopped-since`), so it names the instant
    rather than the volume: the handler forwards every backup's timestamp and the gate raises on the
    earliest one that fails. Same remedy either way — take a fresh backup while stopped.
    """
    e2e_deployment.cli.run_command(
        ["jupyter-deploy", "volume", "backup", "--all"],
        timeout_seconds=_BACKUP_TIMEOUT_SECONDS,
    )

    # Sanity check that these backups are good BEFORE invalidating them, so a failure below cannot be
    # confused with "the backups were never acceptable in the first place".
    e2e_deployment.cli.run_command(
        [
            "jupyter-deploy",
            "config",
            "--restore-volumes",
            "--availability-zone",
            base_availability_zone,
            "--additional-ebs-mounts",
            EBS_MOUNT,
            "--additional-efs-mounts",
            EFS_MOUNT,
        ]
    )

    # Run the app, then quiesce it again: the backups now predate the last shutdown, so they can no
    # longer be shown to hold everything on the volumes.
    e2e_deployment.ensure_server_running(wait_after_restart=True)
    e2e_deployment.cli.run_command(["jupyter-deploy", "host", "stop"])

    with pytest.raises(JDCliError) as excinfo:
        e2e_deployment.cli.run_command(
            [
                "jupyter-deploy",
                "config",
                "--restore-volumes",
                "--availability-zone",
                alt_availability_zone,
                "--additional-ebs-mounts",
                EBS_MOUNT,
                "--additional-efs-mounts",
                EFS_MOUNT,
            ]
        )

    message = " ".join(str(excinfo.value).split())
    assert "was running after" in message, f"Unexpected failure reason: {message}"
    # The instant is named, and it is the one the backups were taken at — a bare "too old" would leave a
    # user unable to tell which of their captures the refusal is about.
    assert "fresh backup" in message, f"The refusal must say how to proceed: {message}"
    assert "Traceback (most recent call last)" not in message


@pytest.mark.order(ORDER_MUTATING_VOLUME_SWAPS + 6)
@pytest.mark.mutating
@skip_if_testvars_not_set(["JD_E2E_ALT_AVAILABILITY_ZONE"])
def test_zone_swap_with_a_restore_preserves_volume_data(
    e2e_deployment: EndToEndDeployment,
    quiesced_with_markers: None,
    alt_availability_zone: str,
) -> None:
    """The full backup-restore-swap cycle: every volume's data survives a change of zone.

    One test rather than several because the steps are not independently meaningful — a backup with
    no swap after it proves nothing about restoration, and a swap with no marker uploaded before it
    cannot tell a restored volume from a fresh one.

    Marker CONTENT is what is asserted. A recreated-empty volume would still have the mount point and
    could still have a same-named file created by a start script, so existence is too weak a check.
    """
    zone_before = get_volume_details(e2e_deployment, HOME_VOLUME)["zone"]
    instance_id_before = e2e_deployment.cli.get_str_output("instance_id")
    home_volume_id_before = get_volume_details(e2e_deployment, HOME_VOLUME)["volume_id"]
    efs_id_before = get_volume_details(e2e_deployment, EFS_VOLUME)["volume_id"]

    # --- back up everything that can be backed up -------------------------------------------------
    e2e_deployment.cli.run_command(
        ["jupyter-deploy", "volume", "backup", "--all"],
        timeout_seconds=_BACKUP_TIMEOUT_SECONDS,
    )

    for name in (HOME_VOLUME, EBS_VOLUME):
        detail = get_volume_details(e2e_deployment, name)
        assert detail["backup_state"] == "completed", (
            f"{name} has no completed backup, so the swap below would lose its data: {detail}"
        )
    # --all skips the EFS mount rather than failing on it: "no backup mechanism" is a property of the
    # kind, so a bulk backup has nothing to do for it. Still `not needed` afterwards is the proof — a
    # `--all` that had tried would have left either a backup id or an error.
    assert get_volume_details(e2e_deployment, EFS_VOLUME)["backup_id"] == "not needed"

    # --- swap the zone, restoring each volume from its own backup ---------------------------------
    e2e_deployment.cli.run_command(
        [
            "jupyter-deploy",
            "config",
            "--restore-volumes",
            "--availability-zone",
            alt_availability_zone,
            "--additional-ebs-mounts",
            EBS_MOUNT,
            "--additional-efs-mounts",
            EFS_MOUNT,
        ]
    )
    e2e_deployment.cli.run_command(["jupyter-deploy", "up", "-y"], timeout_seconds=_APPLY_TIMEOUT_SECONDS)
    e2e_deployment.ensure_server_running(wait_after_restart=True)

    # --- the swap actually happened ---------------------------------------------------------------
    home_after = get_volume_details(e2e_deployment, HOME_VOLUME)
    assert home_after["zone"] == alt_availability_zone, (
        f"Expected the home volume in {alt_availability_zone}, got {home_after['zone']}"
    )
    assert home_after["zone"] != zone_before, "The zone did not change, so nothing below tests a swap"
    assert home_after["volume_id"] != home_volume_id_before, (
        "The home volume was not replaced; a volume cannot change zone in place, so this is not the "
        "path the test claims to exercise"
    )
    assert e2e_deployment.cli.get_str_output("instance_id") != instance_id_before, (
        "The instance was not replaced, but it cannot attach a volume in another zone"
    )

    # --- and the data came with it ----------------------------------------------------------------
    for path in (_HOME_MARKER, _EBS_MARKER, _EFS_MARKER):
        result = e2e_deployment.cli.run_exec_with_retry(["jupyter-deploy", "server", "exec", "--", "cat", path])
        assert _MARKER_CONTENT in e2e_deployment.cli.strip_ansi(result.stdout), (
            f"{path} did not survive the zone swap: the volume was recreated EMPTY rather than restored from its backup"
        )

    # EFS survives for a different reason than the block volumes: it was never replaced at all.
    assert get_volume_details(e2e_deployment, EFS_VOLUME)["volume_id"] == efs_id_before, (
        "The EFS file system was replaced; it is regional and should only have gained a new mount target"
    )
    assert get_volume_names(e2e_deployment) == [HOME_VOLUME, EBS_VOLUME, EFS_VOLUME], (
        "The inventory changed across the swap"
    )

    # NOTE: do NOT "clean up" by clearing ebs_snapshot_ids. `snapshot_id` forces replacement on an
    # aws_ebs_volume, so resetting that variable would destroy these volumes and recreate them empty.
    e2e_deployment.cli.run_command(
        ["jupyter-deploy", "server", "exec", "--", "rm", "-f", _HOME_MARKER, _EBS_MARKER, _EFS_MARKER]
    )
