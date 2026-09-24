"""Parity checks between the logging pipeline's configuration files.

Two hops, each between a pair of files that must agree and that nothing forces to agree:
container -> fluent-bit (a fluentd tag must have a matching route) and fluent-bit -> logrotate (a
file fluent-bit writes must be a file something rotates). Both failure modes are quiet: the first
drops a container's logs, the second lets them grow until the data volume is full and the app dies
for what looks like an unrelated reason.

Kept separate from ``test_services_consistency.py`` on purpose — that file covers the non-logging
service-config parity (the S3 bundle, the command scripts, the manifest's service list).
"""

import re
import unittest
from pathlib import Path

from jupyter_deploy_tf_aws_ec2_base.template import TEMPLATE_PATH

SERVICES_PATH: Path = TEMPLATE_PATH / "services"


class TestLoggingConsistency(unittest.TestCase):
    """The fluent-bit outputs and the docker-compose fluentd logging tags must stay in lockstep.

    Each container that logs via the ``fluentd`` driver carries a ``tag: "docker.<name>"``; fluent-bit
    routes each tag to a file with a matching ``Match docker.<name>``. A tag with no matching output
    silently drops that container's logs; a Match with no tag is a dead rule that will drop the next
    renamed service. This guards both directions.
    """

    COMPOSE_PATH: Path = SERVICES_PATH / "docker-compose.yml.tftpl"
    FLUENT_BIT_PATH: Path = SERVICES_PATH / "fluent-bit" / "fluent-bit.conf"

    def test_compose_tags_match_fluent_bit_outputs(self) -> None:
        compose_tags = set(re.findall(r'tag:\s*"(docker\.[\w-]+)"', self.COMPOSE_PATH.read_text()))
        fluent_bit_matches = set(
            re.findall(r"^\s*Match\s+(docker\.[\w-]+)\s*$", self.FLUENT_BIT_PATH.read_text(), re.M)
        )

        self.assertTrue(compose_tags, "expected at least one fluentd logging tag in docker-compose")
        self.assertEqual(
            compose_tags,
            fluent_bit_matches,
            f"fluentd tags without a fluent-bit output (logs dropped): {compose_tags - fluent_bit_matches}; "
            f"fluent-bit Match rules with no matching container tag (dead rules): {fluent_bit_matches - compose_tags}",
        )


class TestLogRotationConsistency(unittest.TestCase):
    """Every directory fluent-bit writes into must be one the log-rotator actually rotates.

    fluent-bit writes to a container path (``/logs``) that a bind mount maps to a host directory;
    log-rotator rotates host paths by glob. If the two drift, logs accumulate unbounded on the data
    volume until it fills -- which presents as the app dying, not as a logging bug.
    """

    COMPOSE_PATH: Path = SERVICES_PATH / "docker-compose.yml.tftpl"
    FLUENT_BIT_PATH: Path = SERVICES_PATH / "fluent-bit" / "fluent-bit.conf"
    LOGROTATOR_PATH: Path = SERVICES_PATH / "logrotator" / "logrotator-start.sh.tftpl"

    def test_fluent_bit_output_dirs_are_rotated(self) -> None:
        output_dirs = set(re.findall(r"^\s*Path\s+(\S+)\s*$", self.FLUENT_BIT_PATH.read_text(), re.M))
        self.assertTrue(output_dirs, "expected at least one fluent-bit file OUTPUT Path")

        # Resolve each container path to its host path through the compose bind mounts, so a changed
        # mount is caught rather than silently making the comparison pass against a stale hard-coded
        # host directory. A list of pairs, not a dict: the same host directory is mounted by several
        # services at different container paths (/var/log/services is /logs in fluent-bit and
        # /var/log/services in log-rotator), and keying by host path would drop one of them.
        mounts = re.findall(r"^\s*-\s+(/\S+):(/\S+?)(?::ro)?\s*$", self.COMPOSE_PATH.read_text(), re.M)
        rotated_globs = re.findall(r"^(/\S+)\s*\{", self.LOGROTATOR_PATH.read_text(), re.M)
        rotated_dirs = {str(Path(glob).parent) for glob in rotated_globs}

        for container_dir in output_dirs:
            host_dir = next((host for host, container in mounts if container == container_dir), None)
            self.assertIsNotNone(
                host_dir,
                f"fluent-bit writes to {container_dir}, which no docker-compose bind mount maps to a "
                f"host directory; known mounts: {mounts}",
            )
            self.assertIn(
                host_dir,
                rotated_dirs,
                f"fluent-bit writes to {container_dir} -> host {host_dir}, which the log-rotator does "
                f"not rotate (it rotates {sorted(rotated_dirs)}); logs there grow unbounded",
            )
