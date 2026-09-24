"""Helpers for reading a deployment's storage volumes through `jd volume`.

Deliberately thin, and deliberately NOT used by the tests that are about `jd volume list` or
`jd volume show` themselves — a test asserting what `list` returns should issue the command it is
testing, or it ends up asserting that a helper agrees with itself. These are for the tests that need
a volume's state as a *precondition* (does it have a backup, what zone is it in) rather than as the
thing under test.
"""

import json
from typing import Any

from pytest_jupyter_deploy.deployment import EndToEndDeployment


def get_volume_names(e2e_deployment: EndToEndDeployment) -> list[str]:
    """Return the identity of every volume the deployment mounts.

    `volume list --json` emits a list of `{name, type, description}` objects, like
    `component list --json`; this projects out the names for callers that only need identities.
    """
    result = e2e_deployment.cli.run_command(["jupyter-deploy", "volume", "list", "--json"])
    volumes = json.loads(result.stdout)
    assert isinstance(volumes, list), f"Expected a list of volume objects, got {type(volumes)}"
    return [volume["name"] for volume in volumes]


def get_volume_details(e2e_deployment: EndToEndDeployment, name: str | None = None) -> dict[str, Any]:
    """Return `jd volume show --json` for one volume, or for the default volume when name is None.

    Passing no name is meaningful: the default comes from the template manifest, so a caller that
    wants "whatever volume this deployment is fundamentally about" must omit --name rather than
    guess a name.
    """
    cmd = ["jupyter-deploy", "volume", "show", "--json"]
    if name is not None:
        cmd += ["--name", name]
    result = e2e_deployment.cli.run_command(cmd)
    details = json.loads(result.stdout)
    assert isinstance(details, dict), f"Expected a JSON object, got {type(details)}"
    return details
