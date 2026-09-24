import unittest

from jupyter_deploy import manifest_validation
from jupyter_deploy.exceptions import InvalidCommandGrammarError
from jupyter_deploy.manifest import VOLUME_READINESS_COMMAND, JupyterDeployCommandV1, JupyterDeployManifestV1


def _valid_pool_status() -> JupyterDeployCommandV1:
    return JupyterDeployCommandV1.model_validate(
        {
            "cmd": "pool.status",
            "flags": [
                {
                    "name": "is-mng",
                    "conditions": [
                        {
                            "left": {"source": "cli", "source-key": "name"},
                            "operator": "in",
                            "right": {"source": "output", "source-key": "platform_mng_names"},
                        }
                    ],
                }
            ],
            "sequence": [
                {"api-name": "k8s.custom.get-cluster", "when": "!is-mng", "arguments": []},
                {"api-name": "aws.eks.describe-nodegroup", "when": "is-mng", "arguments": []},
                {"api-name": "core.coalesce-str", "arguments": []},
            ],
        }
    )


class TestValidCommands(unittest.TestCase):
    def test_valid_pool_status_passes(self) -> None:
        manifest_validation.validate_command(_valid_pool_status())  # no raise

    def test_command_without_flags_passes(self) -> None:
        cmd = JupyterDeployCommandV1.model_validate(
            {"cmd": "pool.list", "sequence": [{"api-name": "k8s.custom.list-cluster", "arguments": []}]}
        )
        manifest_validation.validate_command(cmd)  # no raise


def _backup_like(gate: bool) -> dict:
    """A create-then-wait command, optionally preceded by a gate step, with CORRECT indices.

    Shaped after `volume.backup`: inserting the gate shifts every later reference by one, which is the
    mistake this validation exists to catch.
    """
    steps: list[dict] = []
    if gate:
        steps.append({"api-name": "aws.ec2.verify-instance-stopped", "arguments": []})
    offset = 1 if gate else 0
    steps.append({"api-name": "aws.ec2.create-snapshot", "arguments": []})
    steps.append(
        {
            "api-name": "aws.ec2.wait-snapshot-completed",
            "arguments": [{"api-attribute": "snapshot_id", "source": "result", "source-key": f"[{offset}].SnapshotId"}],
        }
    )
    return {
        "cmd": "volume.backup",
        "sequence": steps,
        "results": [
            {"result-name": "volume.backup.backup_id", "source": "result", "source-key": f"[{offset + 1}].SnapshotId"}
        ],
    }


class TestStepReferences(unittest.TestCase):
    """`source-key: '[N].Field'` is POSITIONAL, so a step insertion invalidates later references."""

    def test_correct_indices_pass_without_a_gate(self) -> None:
        manifest_validation.validate_command(JupyterDeployCommandV1.model_validate(_backup_like(gate=False)))

    def test_correct_indices_pass_with_a_gate(self) -> None:
        manifest_validation.validate_command(JupyterDeployCommandV1.model_validate(_backup_like(gate=True)))

    def test_stale_index_after_removing_a_step_is_rejected(self) -> None:
        """Deleting a step leaves later references past the end, which IS caught."""
        cmd_dict = _backup_like(gate=True)
        del cmd_dict["sequence"][0]
        cmd = JupyterDeployCommandV1.model_validate(cmd_dict)

        with self.assertRaises(InvalidCommandGrammarError):
            manifest_validation.validate_command(cmd)

    def test_a_shifted_but_in_bounds_reference_is_NOT_caught(self) -> None:
        """Known gap, pinned so nobody assumes otherwise.

        Inserting a step shifts later references to a *different but still valid* index, so bounds
        checking cannot see it: `[0].SnapshotId` after a gate lands at [0] points at the gate, which is
        in range. Catching this needs a static api-name -> result-names registry so the FIELD can be
        checked against the referenced step, not just the index. Until then, the manifest's own
        `[N]` comments and review are the only defense.
        """
        cmd_dict = _backup_like(gate=True)
        cmd_dict["sequence"][2]["arguments"][0]["source-key"] = "[0].SnapshotId"
        cmd = JupyterDeployCommandV1.model_validate(cmd_dict)

        manifest_validation.validate_command(cmd)  # no raise -- documents the limitation

    def test_a_step_cannot_reference_itself(self) -> None:
        cmd = JupyterDeployCommandV1.model_validate(
            {
                "cmd": "c",
                "sequence": [
                    {
                        "api-name": "a.b.c",
                        "arguments": [{"api-attribute": "x", "source": "result", "source-key": "[0].Y"}],
                    }
                ],
            }
        )
        with self.assertRaises(InvalidCommandGrammarError) as ctx:
            manifest_validation.validate_command(cmd)
        self.assertTrue(any("itself or later" in v for v in ctx.exception.violations))

    def test_a_result_past_the_end_of_the_sequence_is_rejected(self) -> None:
        cmd = JupyterDeployCommandV1.model_validate(
            {
                "cmd": "c",
                "sequence": [{"api-name": "a.b.c", "arguments": []}],
                "results": [{"result-name": "c.v", "source": "result", "source-key": "[3].V"}],
            }
        )
        with self.assertRaises(InvalidCommandGrammarError) as ctx:
            manifest_validation.validate_command(cmd)
        self.assertTrue(any("has 1 step(s)" in v for v in ctx.exception.violations))

    def test_a_result_source_key_naming_no_step_is_rejected(self) -> None:
        cmd = JupyterDeployCommandV1.model_validate(
            {
                "cmd": "c",
                "sequence": [{"api-name": "a.b.c", "arguments": []}],
                "results": [{"result-name": "c.v", "source": "result", "source-key": "SnapshotId"}],
            }
        )
        with self.assertRaises(InvalidCommandGrammarError):
            manifest_validation.validate_command(cmd)

    def test_non_result_sources_are_untouched(self) -> None:
        """cli/output/literal arguments have no step semantics, so they must not be index-checked."""
        cmd = JupyterDeployCommandV1.model_validate(
            {
                "cmd": "c",
                "sequence": [
                    {
                        "api-name": "a.b.c",
                        "arguments": [
                            {"api-attribute": "x", "source": "cli", "source-key": "name"},
                            {"api-attribute": "y", "source": "output", "source-key": "instance_id"},
                            {"api-attribute": "z", "source": "literal", "value": "v"},
                        ],
                    }
                ],
            }
        )
        manifest_validation.validate_command(cmd)  # no raise


class TestRejections(unittest.TestCase):
    def _assert_violation(self, cmd_dict: dict, needle: str) -> None:
        cmd = JupyterDeployCommandV1.model_validate(cmd_dict)
        with self.assertRaises(InvalidCommandGrammarError) as ctx:
            manifest_validation.validate_command(cmd)
        self.assertTrue(
            any(needle in v for v in ctx.exception.violations),
            f"expected a violation containing {needle!r}, got {ctx.exception.violations}",
        )

    def test_when_references_undefined_flag(self) -> None:
        self._assert_violation(
            {
                "cmd": "c",
                "flags": [
                    {
                        "name": "is-mng",
                        "conditions": [
                            {
                                "left": {"source": "cli", "source-key": "name"},
                                "operator": "in",
                                "right": {"source": "output", "source-key": "mng"},
                            }
                        ],
                    }
                ],
                "sequence": [{"api-name": "k8s.custom.get-cluster", "when": "not-a-flag", "arguments": []}],
            },
            "undefined flag",
        )

    def test_duplicate_flag_name(self) -> None:
        cond = {
            "left": {"source": "cli", "source-key": "name"},
            "operator": "in",
            "right": {"source": "output", "source-key": "mng"},
        }
        self._assert_violation(
            {
                "cmd": "c",
                "flags": [
                    {"name": "dup", "conditions": [cond]},
                    {"name": "dup", "conditions": [cond]},
                ],
                "sequence": [],
            },
            "duplicate flag name",
        )

    def test_unknown_operator(self) -> None:
        self._assert_violation(
            {
                "cmd": "c",
                "flags": [
                    {
                        "name": "f",
                        "conditions": [
                            {
                                "left": {"source": "cli", "source-key": "name"},
                                "operator": "not-in",
                                "right": {"source": "output", "source-key": "mng"},
                            }
                        ],
                    }
                ],
                "sequence": [],
            },
            "unknown operator",
        )

    def test_literal_operand_missing_value(self) -> None:
        self._assert_violation(
            {
                "cmd": "c",
                "flags": [
                    {
                        "name": "f",
                        "conditions": [
                            {
                                "left": {"source": "literal"},
                                "operator": "in",
                                "right": {"source": "output", "source-key": "mng"},
                            }
                        ],
                    }
                ],
                "sequence": [],
            },
            "requires 'value'",
        )

    def test_in_with_literal_scalar_right_operand(self) -> None:
        self._assert_violation(
            {
                "cmd": "c",
                "flags": [
                    {
                        "name": "f",
                        "conditions": [
                            {
                                "left": {"source": "cli", "source-key": "name"},
                                "operator": "in",
                                "right": {"source": "literal", "value": "scalar"},
                            }
                        ],
                    }
                ],
                "sequence": [],
            },
            "must be list-typed",
        )

    def test_flag_condition_referencing_instruction_result(self) -> None:
        # Flags are computed before the sequence runs, so a `source: result` operand would
        # always resolve against an empty result set. Reject it statically.
        self._assert_violation(
            {
                "cmd": "c",
                "flags": [
                    {
                        "name": "f",
                        "conditions": [
                            {
                                "left": {"source": "result", "source-key": "[0].Name"},
                                "operator": "in",
                                "right": {"source": "output", "source-key": "mng"},
                            }
                        ],
                    }
                ],
                "sequence": [],
            },
            "must not depend on instruction results",
        )

    def test_validate_manifest_aggregates_across_commands(self) -> None:
        manifest = JupyterDeployManifestV1.model_validate(
            {
                "schema_version": 1,
                "template": {"name": "t", "engine": "terraform", "version": "1"},
                "commands": [
                    {
                        "cmd": "bad",
                        "sequence": [{"api-name": "k8s.custom.get-cluster", "when": "ghost", "arguments": []}],
                    }
                ],
            }
        )
        with self.assertRaises(InvalidCommandGrammarError):
            manifest_validation.validate_manifest(manifest)


class TestVolumeReadinessDeclaration(unittest.TestCase):
    """Omitting the readiness command is an opt-out everywhere except one combination."""

    @staticmethod
    def _manifest(volumes: dict, commands: list | None = None) -> JupyterDeployManifestV1:
        return JupyterDeployManifestV1(
            **{  # type: ignore[arg-type]
                "schema_version": 1,
                "template": {"name": "t", "engine": "terraform", "version": "1.0.0"},
                "volumes": volumes,
                "commands": commands or [],
            }
        )

    _WITH_MAP = {"static": [{"name": "home", "volume-id-value": "v", "backups-map": "ids"}]}

    def test_a_backups_map_without_the_readiness_command_is_refused(self) -> None:
        """`--restore-volumes` can replace this volume, and nothing would check the backup is current."""
        manifest = self._manifest(self._WITH_MAP)

        violations = manifest_validation.collect_volume_violations(manifest)

        self.assertEqual(len(violations), 1)
        self.assertIn("home", violations[0])
        self.assertIn(VOLUME_READINESS_COMMAND, violations[0])

    def test_declaring_the_command_satisfies_it(self) -> None:
        manifest = self._manifest(self._WITH_MAP, [{"cmd": VOLUME_READINESS_COMMAND, "sequence": []}])

        self.assertEqual(manifest_validation.collect_volume_violations(manifest), [])

    def test_no_backups_map_needs_no_command(self) -> None:
        """A kind with no backup mechanism is never restored from, so there is nothing to check."""
        manifest = self._manifest({"static": [{"name": "home", "volume-id-value": "v"}]})

        self.assertEqual(manifest_validation.collect_volume_violations(manifest), [])

    def test_a_template_with_no_volumes_is_unaffected(self) -> None:
        manifest = JupyterDeployManifestV1(
            **{  # type: ignore[arg-type]
                "schema_version": 1,
                "template": {"name": "t", "engine": "terraform", "version": "1.0.0"},
            }
        )

        self.assertEqual(manifest_validation.collect_volume_violations(manifest), [])

    def test_a_dynamic_group_is_reported_by_group_name(self) -> None:
        manifest = self._manifest(
            {
                "dynamic": [
                    {
                        "group": "extra-ebs",
                        "inventory-value": "inv",
                        "name-path": ".name",
                        "volume-id-path": ".id",
                        "backups-map": "ids",
                    }
                ]
            }
        )

        violations = manifest_validation.collect_volume_violations(manifest)

        self.assertEqual(len(violations), 1)
        self.assertIn("extra-ebs", violations[0])
