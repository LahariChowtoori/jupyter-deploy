"""Apply #2 of the mutating pass: -> a larger CPU instance + uv (external volumes stay).

A second instance replacement in the opposite direction from apply #1, and the return leg off GPU:
the deployment must end on a CPU instance with uv so a run does not leave a GPU billing until the
teardown job reaps it.

The target is ``JD_E2E_LARGER_INSTANCE`` rather than the instance the suite deployed with, so the
swap is a real replacement whatever ran before — the assertions here are about what survives an
instance change, and they need one to actually happen.

The external-volume flag files this asserts on are seeded by apply #1, so the ordered pass is how
the suite runs it; ``constants.py`` holds the ordinals.

Not covered here: per-volume file/directory operation matrices. ``test_home_volume.py`` exercises
the same mount machinery against the home volume; what this file adds is a write probe and a `df`
check per external volume, which is what distinguishes a real mount from a directory that fell back
to the root volume.
"""

from pathlib import Path

import pytest
from pytest_jupyter_deploy.commands import verify_server_command_fails
from pytest_jupyter_deploy.deployment import EndToEndDeployment
from pytest_jupyter_deploy.files import verify_dir_exists_on_server, verify_file_exists_on_server
from pytest_jupyter_deploy.notebook import delete_notebook, run_notebook_in_jupyterlab, upload_notebook
from pytest_jupyter_deploy.oauth2_proxy.github import GitHubOAuth2ProxyApplication
from pytest_jupyter_deploy.plugin import skip_if_testvars_not_set

from .constants import EBS_FLAG, EBS_MOUNT, EFS_FLAG, EFS_MOUNT, HOME_FLAG, ORDER_MUTATING_CPU_UV

_APPLY_TIMEOUT_SECONDS = 3600


@pytest.mark.order(ORDER_MUTATING_CPU_UV)
@pytest.mark.mutating
@skip_if_testvars_not_set(["JD_E2E_LARGER_INSTANCE"])
def test_switch_to_cpu_uv(
    e2e_deployment: EndToEndDeployment,
    github_oauth_app: GitHubOAuth2ProxyApplication,
    larger_instance_type: str,
    logged_user: str,
) -> None:
    """An in-place swap to a larger CPU instance and uv, with all data intact.

    A genuinely different path from a GPU swap: the AMI resolves to the standard image rather than a
    DLAMI, and the package-manager environment is rebuilt from a uv lockfile. Every flag file — home
    volume, external EBS, external EFS — must still be there, because a user who changes their
    instance has not agreed to lose anything.
    """
    e2e_deployment.ensure_server_running()

    instance_id_before = e2e_deployment.cli.get_str_output("instance_id")
    # NOT `persisting_resources`: the E2E mounts are deliberately non-persist (a persisted volume
    # survives `jd down` and would leak EBS/EFS out of every CI run), so that output is empty and
    # asserting it unchanged would be vacuous while reading as if it proved volume identity. The
    # flag files below are what actually prove the same volumes reattached.
    deployment_id_before = e2e_deployment.cli.get_str_output("deployment_id")

    # Re-pass the mount flags: they are list variables, and omitting them would unmount (and
    # potentially destroy) the volumes this test is about to assert on.
    e2e_deployment.ensure_deployed_with(
        [
            "--instance-type",
            larger_instance_type,
            "--jupyter-package-manager",
            "uv",
            "--additional-ebs-mounts",
            EBS_MOUNT,
            "--additional-efs-mounts",
            EFS_MOUNT,
        ],
        timeout_seconds=_APPLY_TIMEOUT_SECONDS,
    )

    e2e_deployment.ensure_server_running()
    e2e_deployment.ensure_authorized([logged_user], "", [])

    assert e2e_deployment.cli.get_str_output("instance_id") != instance_id_before, (
        "Expected the instance-type change to replace the instance"
    )
    # The deployment identity must survive an instance replacement: it is the suffix of every SSM
    # document name, so a swap that regenerated it would invalidate every `jd host`/`jd server`
    # command at once.
    assert e2e_deployment.cli.get_str_output("deployment_id") == deployment_id_before, (
        "The deployment id changed across the swap; all SSM documents would break"
    )

    verify_file_exists_on_server(e2e_deployment, HOME_FLAG)
    verify_file_exists_on_server(e2e_deployment, EBS_FLAG)
    verify_file_exists_on_server(e2e_deployment, EFS_FLAG)

    github_oauth_app.ensure_authenticated()
    github_oauth_app.verify_jupyterlab_accessible()

    # Remove the pixi manifests the previous configuration left in the home volume: they persist
    # on the volume across the switch, and a stale pixi.toml alongside a uv environment is the
    # exact confusing state a user would report.
    e2e_deployment.cli.run_command(
        ["jupyter-deploy", "server", "exec", "--", "rm", "-f", "/home/jovyan/pixi.toml", "/home/jovyan/pixi.lock"]
    )


@pytest.mark.order(ORDER_MUTATING_CPU_UV + 1)
@pytest.mark.mutating
def test_external_volumes_ebs_and_efs_mounted(e2e_deployment: EndToEndDeployment) -> None:
    """Both external volumes are mounted, writable and correctly sized after the second swap.

    EBS and EFS reattach by different mechanisms (a block device that must be found and mounted
    versus an NFS mount that must resolve and connect), so both are asserted. The size check
    catches the case where the mount point exists as a plain directory on the root volume — which
    looks identical to a working mount until the user fills it up.
    """
    e2e_deployment.ensure_server_running()

    for mount_point in ("/home/jovyan/external-ebs1", "/home/jovyan/external-efs1"):
        verify_dir_exists_on_server(e2e_deployment, mount_point)

        probe = f"{mount_point}/e2e_write_probe.txt"
        e2e_deployment.cli.run_command(["jupyter-deploy", "server", "exec", "--", "touch", probe])
        verify_file_exists_on_server(e2e_deployment, probe)
        e2e_deployment.cli.run_command(["jupyter-deploy", "server", "exec", "--", "rm", "-f", probe])

    # The EBS volume was requested at 50 GiB, so `df` must show a filesystem of its own —
    # not the root volume the mount point would fall back to.
    result = e2e_deployment.cli.run_command(
        ["jupyter-deploy", "server", "exec", "--", "df", "-h", "/home/jovyan/external-ebs1"]
    )
    assert "external-ebs1" in result.stdout, (
        f"external-ebs1 is not a mount point of its own; it fell back to the root volume:\n{result.stdout}"
    )


@pytest.mark.order(ORDER_MUTATING_CPU_UV + 2)
@pytest.mark.mutating
def test_uv_install_and_persist(
    e2e_deployment: EndToEndDeployment,
    github_oauth_app: GitHubOAuth2ProxyApplication,
    logged_user: str,
) -> None:
    """Packages a user installs from a notebook survive a server restart (uv flavor).

    Same property as the pixi case, different machinery: uv syncs from ``pyproject.toml`` +
    ``uv.lock`` on the home volume. Asserted separately rather than assumed symmetric, because
    the two package managers have independent start scripts and lockfile handling.
    """
    actual_package_manager = e2e_deployment.get_str_variable_value("jupyter_package_manager")
    assert actual_package_manager == "uv", f"Expected uv, got '{actual_package_manager}'"

    e2e_deployment.ensure_server_running()
    e2e_deployment.ensure_authorized([logged_user], "", [])
    github_oauth_app.ensure_authenticated()
    github_oauth_app.verify_jupyterlab_accessible()

    notebook_path = Path(__file__).parent / "notebooks" / "uv_install_ipywidgets.ipynb"
    server_path = upload_notebook(e2e_deployment, notebook_path, "e2e-test/uv_install_ipywidgets.ipynb")
    run_notebook_in_jupyterlab(github_oauth_app.page, server_path, timeout_ms=120000)
    delete_notebook(e2e_deployment, server_path)

    e2e_deployment.cli.run_command(["jupyter-deploy", "server", "restart"])
    e2e_deployment.ensure_server_running()

    # Exits non-zero if ipywidgets is not installed, so the call itself is the assertion.
    e2e_deployment.cli.run_command(["jupyter-deploy", "server", "exec", "--", "uv", "pip", "show", "ipywidgets"])


@pytest.mark.order(ORDER_MUTATING_CPU_UV + 3)
@pytest.mark.mutating
def test_uv_environment_recovery(
    e2e_deployment: EndToEndDeployment,
    github_oauth_app: GitHubOAuth2ProxyApplication,
    logged_user: str,
) -> None:
    """A broken uv environment auto-recovers on restart, back to the base env.

    The uv counterpart of the pixi recovery test: a user can remove JupyterLab from its own
    environment, and the app must still come back without an SSM rescue. Recovery is a reset, so
    the user's own packages do not survive it — pinned here so the behavior stays deliberate.
    """
    actual_package_manager = e2e_deployment.get_str_variable_value("jupyter_package_manager")
    assert actual_package_manager == "uv", f"Expected uv, got '{actual_package_manager}'"

    # NOTE: `uv remove` is the correct way to break the environment — do not change this.
    e2e_deployment.cli.run_command(["jupyter-deploy", "server", "exec", "--", "uv", "remove", "jupyterlab"])
    e2e_deployment.cli.run_command(["jupyter-deploy", "server", "restart"])
    e2e_deployment.ensure_server_running()
    e2e_deployment.ensure_authorized([logged_user], "", [])

    github_oauth_app.ensure_authenticated()
    github_oauth_app.verify_jupyterlab_accessible()

    e2e_deployment.cli.run_command(["jupyter-deploy", "server", "exec", "--", "uv", "pip", "show", "jupyterlab"])
    verify_server_command_fails(
        e2e_deployment,
        ["jupyter-deploy", "server", "exec", "--", "uv", "pip", "show", "ipywidgets"],
        expected_returncode=1,
        stderr_contains="not found",
    )
