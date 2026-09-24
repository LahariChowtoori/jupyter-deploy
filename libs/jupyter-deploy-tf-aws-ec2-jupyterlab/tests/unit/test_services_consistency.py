"""Cross-file parity checks for the jupyterlab template's service configuration.

These are cheap file-parsing tests, not terraform evaluation. Each one guards a pair of files that
must agree but that nothing forces to agree: the bundle upload list and the bundle download loop,
the manifest's service list and the compose containers, the command documents and the scripts they
invoke. Every one of them has a failure mode that a `terraform plan` accepts happily and that only
shows up as a missing file or a dead command on a deployed instance.

Deliberately assertions about *classes* of drift rather than fixed lists: adding a service or a
script should either be covered automatically or fail loudly, never quietly half-wired.

The logging pipeline's own parity checks live in ``test_logging_consistency.py``.
"""

import re
import unittest
from pathlib import Path

import yaml

from jupyter_deploy_tf_aws_ec2_jupyterlab.template import TEMPLATE_PATH

SERVICES_PATH: Path = TEMPLATE_PATH / "services"
ENGINE_PATH: Path = TEMPLATE_PATH / "engine"

# Both values `local.jupyter_toml_filename` can take. The uploaded object name is chosen by
# var.jupyter_package_manager, so a text-level parse sees the terraform expression rather than a
# name; expanding it here keeps the bundle test honest for both flavors.
JUPYTER_TOML_FILENAMES = frozenset({"pyproject.jupyter.toml", "pixi.jupyter.toml"})


class TestManifestServicesConsistency(unittest.TestCase):
    """Every service the manifest exposes to `jd server` must exist in docker-compose.

    `jd server logs -s <name>` and friends run `docker logs <name>` on the instance, so a manifest
    name with no container is a command that always fails. Not an equality: fluent-bit and
    log-rotator are infrastructure, deliberately not user-addressable services.
    """

    COMPOSE_PATH: Path = SERVICES_PATH / "docker-compose.yml.tftpl"
    MANIFEST_PATH: Path = TEMPLATE_PATH / "manifest.yaml"

    def test_manifest_services_are_compose_services(self) -> None:
        compose_services = set(re.findall(r"^\s{4}container_name:\s*(\S+)\s*$", self.COMPOSE_PATH.read_text(), re.M))
        manifest_services = set(yaml.safe_load(self.MANIFEST_PATH.read_text())["services"])

        self.assertTrue(compose_services, "expected at least one container_name in docker-compose")
        self.assertTrue(
            manifest_services <= compose_services,
            f"manifest declares services with no matching container: {manifest_services - compose_services}; "
            f"compose defines {sorted(compose_services)}",
        )


class TestDeploymentBundleConsistency(unittest.TestCase):
    """Every file uploaded to S3 must be downloaded by the startup document, and vice versa.

    ``local.all_script_files`` is the upload side; the startup document's ``aws s3 cp`` calls are the
    download side, driven by hand-maintained filename lists plus a handful of explicit copies.
    Adding an upload without adding it to a list ships a file the instance never fetches; adding a
    name to a list without the upload makes the instance's `aws s3 cp` 404 and fails the whole boot.
    Neither is visible to `terraform validate`.

    The download side is discovered from the document's ``for … in ${join(" ", local.<list>)}`` loops
    rather than from a hardcoded set of list names, so a template that adds a loop is covered without
    editing this test. Naming the lists directly would report a new loop's files as never-fetched.
    """

    SERVICES_TF_PATH: Path = ENGINE_PATH / "services.tf"

    def setUp(self) -> None:
        self.services_tf = self.SERVICES_TF_PATH.read_text()

    def _uploaded_keys(self) -> set[str]:
        """Return the S3 object keys ``local.all_script_files`` declares, with the toml name expanded."""
        keys = set(re.findall(r'"(deployment-(?:scripts|docker)/[^"]+)"\s*=\s*\{', self.services_tf))
        expanded = {key for key in keys if "${" not in key}
        for key in keys - expanded:
            self.assertIn(
                "local.jupyter_toml_filename",
                key,
                f"unrecognized interpolated upload key {key!r}: teach this test how to expand it",
            )
            expanded |= {f"deployment-docker/{name}" for name in JUPYTER_TOML_FILENAMES}
        return expanded

    def _downloaded_keys(self) -> set[str]:
        """Return the S3 object keys the startup document fetches, from both loops and explicit copies."""
        downloaded = set()

        # Every `for <var> in ${join(" ", local.<list>)}; do … aws s3 cp …/<prefix>/$<var> … done`
        # loop, paired with the prefix that loop actually copies into. Discovered rather than listed,
        # so a template that adds a loop is covered without editing this test.
        loops = re.findall(
            r'for\s+(\w+)\s+in\s+\$\{join\(" ", local\.(\w+)\)\}; do(.*?)done',
            self.services_tf,
            re.S,
        )
        self.assertTrue(loops, "expected at least one bundle download loop in the startup document")

        for var, local_name, body in loops:
            prefixes = re.findall(rf"aws s3 cp s3://\S+?/(deployment-\S*?)/\${var}\b", body)
            self.assertTrue(prefixes, f"loop over local.{local_name} does not `aws s3 cp` its ${var}")

            match = re.search(rf"{local_name}\s*=\s*\[(.*?)\]", self.services_tf, re.S)
            self.assertIsNotNone(match, f"could not find local.{local_name} in services.tf")
            assert match is not None  # for mypy; assertIsNotNone above is the real check
            names = re.findall(r'"([^"]+)"', match.group(1))
            for prefix in prefixes:
                downloaded |= {f"{prefix}/{name}" for name in names}

        # Explicit one-off copies (the static assets, docker-startup.sh, the package-manager toml).
        for path in re.findall(r"aws s3 cp s3://[^/]+/(deployment-(?:scripts|docker)/\S+?)\s", self.services_tf):
            if "$" not in path:
                downloaded.add(path)
            elif "local.jupyter_toml_filename" in path:
                downloaded |= {f"deployment-docker/{name}" for name in JUPYTER_TOML_FILENAMES}

        return downloaded

    def test_every_upload_is_downloaded_and_every_download_is_uploaded(self) -> None:
        uploaded = self._uploaded_keys()
        downloaded = self._downloaded_keys()

        self.assertTrue(uploaded, "expected local.all_script_files to declare uploads")
        self.assertEqual(
            uploaded,
            downloaded,
            f"uploaded to S3 but never fetched by the instance (dead payload): {sorted(uploaded - downloaded)}; "
            f"fetched by the instance but never uploaded (boot fails on 404): {sorted(downloaded - uploaded)}",
        )


class TestCommandScriptsConsistency(unittest.TestCase):
    """Every script the SSM command documents invoke must be on the instance at that path.

    The documents run ``sh /usr/local/bin/<script>``; the startup document copies
    ``deployment-scripts/<script>`` there. A document naming a script that is not in the bundle is a
    `jd` command that fails at runtime with "No such file or directory" -- and nothing else connects
    the two files.
    """

    COMMANDS_TF_PATH: Path = ENGINE_PATH / "commands.tf"
    SERVICES_TF_PATH: Path = ENGINE_PATH / "services.tf"
    COMMANDS_DIR: Path = SERVICES_PATH / "commands"

    def setUp(self) -> None:
        self.invoked = set(re.findall(r"/usr/local/bin/([\w.-]+\.sh)", self.COMMANDS_TF_PATH.read_text()))

    def test_invoked_scripts_are_in_the_deployment_bundle(self) -> None:
        match = re.search(r"deployment_scripts_filenames\s*=\s*\[(.*?)\]", self.SERVICES_TF_PATH.read_text(), re.S)
        self.assertIsNotNone(match, "could not find local.deployment_scripts_filenames in services.tf")
        assert match is not None  # for mypy; assertIsNotNone above is the real check
        bundled = set(re.findall(r'"([^"]+)"', match.group(1)))

        self.assertTrue(self.invoked, "expected commands.tf to invoke at least one /usr/local/bin script")
        self.assertTrue(
            self.invoked <= bundled,
            f"SSM documents invoke scripts that are never copied to /usr/local/bin: {sorted(self.invoked - bundled)}",
        )

    def test_invoked_scripts_exist_in_the_repo(self) -> None:
        for script in sorted(self.invoked):
            # `.tftpl` because some command scripts are rendered by terraform rather than copied
            # verbatim; either form satisfies the document's reference.
            on_disk = (self.COMMANDS_DIR / script).exists() or (self.COMMANDS_DIR / f"{script}.tftpl").exists()
            self.assertTrue(
                on_disk,
                f"commands.tf invokes {script}, which does not exist in services/commands/ "
                f"(as either {script} or {script}.tftpl)",
            )
