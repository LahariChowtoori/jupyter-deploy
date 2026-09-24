import unittest

from jupyter_deploy.engine.outdefs import (
    ListStrTemplateOutputDefinition,
    StrTemplateOutputDefinition,
    TemplateOutputDefinition,
)
from jupyter_deploy.exceptions import InvalidInstructionArgumentError, OutputNotFoundError
from jupyter_deploy.manifest import JupyterDeployFlagV1
from jupyter_deploy.provider import condition_utils
from jupyter_deploy.provider.resolved_clidefs import ResolvedCliParameter, StrResolvedCliParameter


def _mng_flag() -> JupyterDeployFlagV1:
    return JupyterDeployFlagV1.model_validate(
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
    )


def _outputs(names: list[str]) -> dict[str, TemplateOutputDefinition]:
    return {"platform_mng_names": ListStrTemplateOutputDefinition(output_name="platform_mng_names", value=names)}


def _cli(name: str) -> dict[str, ResolvedCliParameter]:
    return {"name": StrResolvedCliParameter(parameter_name="name", value=name)}


class TestEvaluateFlagIn(unittest.TestCase):
    def test_in_true_when_name_in_list(self) -> None:
        result = condition_utils.evaluate_flag(
            _mng_flag(), output_defs=_outputs(["ng-a", "ng-b"]), cli_paramdefs=_cli("ng-a"), resolved_resultdefs={}
        )
        self.assertTrue(result)

    def test_in_false_when_name_not_in_list(self) -> None:
        result = condition_utils.evaluate_flag(
            _mng_flag(), output_defs=_outputs(["ng-a"]), cli_paramdefs=_cli("karpenter-pool"), resolved_resultdefs={}
        )
        self.assertFalse(result)

    def test_missing_output_raises_output_not_found(self) -> None:
        with self.assertRaises(OutputNotFoundError) as ctx:
            condition_utils.evaluate_flag(
                _mng_flag(), output_defs={}, cli_paramdefs=_cli("ng-a"), resolved_resultdefs={}
            )
        self.assertEqual(ctx.exception.output_name, "platform_mng_names")

    def test_right_operand_not_a_list_raises(self) -> None:
        flag = JupyterDeployFlagV1.model_validate(
            {
                "name": "bad",
                "conditions": [
                    {
                        "left": {"source": "cli", "source-key": "name"},
                        "operator": "in",
                        "right": {"source": "output", "source-key": "scalar_out"},
                    }
                ],
            }
        )
        outputs: dict[str, TemplateOutputDefinition] = {
            "scalar_out": StrTemplateOutputDefinition(output_name="scalar_out", value="not-a-list")
        }
        with self.assertRaises(InvalidInstructionArgumentError):
            condition_utils.evaluate_flag(flag, output_defs=outputs, cli_paramdefs=_cli("x"), resolved_resultdefs={})

    def test_left_operand_a_list_raises(self) -> None:
        flag = JupyterDeployFlagV1.model_validate(
            {
                "name": "bad",
                "conditions": [
                    {
                        "left": {"source": "output", "source-key": "platform_mng_names"},
                        "operator": "in",
                        "right": {"source": "output", "source-key": "platform_mng_names"},
                    }
                ],
            }
        )
        with self.assertRaises(InvalidInstructionArgumentError):
            condition_utils.evaluate_flag(
                flag, output_defs=_outputs(["a"]), cli_paramdefs=_cli("x"), resolved_resultdefs={}
            )


def _kind_flag() -> JupyterDeployFlagV1:
    return JupyterDeployFlagV1.model_validate(
        {
            "name": "is-efs",
            "conditions": [
                {
                    "left": {"source": "cli", "source-key": "name"},
                    "operator": "equals",
                    "right": {"source": "literal", "value": "efs"},
                }
            ],
        }
    )


class TestEvaluateFlagEquals(unittest.TestCase):
    def test_equals_true_on_an_exact_match(self) -> None:
        result = condition_utils.evaluate_flag(
            _kind_flag(), output_defs={}, cli_paramdefs=_cli("efs"), resolved_resultdefs={}
        )
        self.assertTrue(result)

    def test_equals_is_case_sensitive(self) -> None:
        result = condition_utils.evaluate_flag(
            _kind_flag(), output_defs={}, cli_paramdefs=_cli("EFS"), resolved_resultdefs={}
        )
        self.assertFalse(result)

    def test_equals_false_on_a_different_value(self) -> None:
        result = condition_utils.evaluate_flag(
            _kind_flag(), output_defs={}, cli_paramdefs=_cli("ebs"), resolved_resultdefs={}
        )
        self.assertFalse(result)

    def test_list_operand_raises_rather_than_comparing_across_types(self) -> None:
        flag = JupyterDeployFlagV1.model_validate(
            {
                "name": "bad",
                "conditions": [
                    {
                        "left": {"source": "output", "source-key": "platform_mng_names"},
                        "operator": "equals",
                        "right": {"source": "literal", "value": "efs"},
                    }
                ],
            }
        )
        with self.assertRaises(InvalidInstructionArgumentError):
            condition_utils.evaluate_flag(
                flag, output_defs=_outputs(["a"]), cli_paramdefs=_cli("x"), resolved_resultdefs={}
            )


class TestEvaluateWhen(unittest.TestCase):
    def test_plain_flag_returns_value(self) -> None:
        self.assertTrue(condition_utils.evaluate_when("is-mng", {"is-mng": True}))
        self.assertFalse(condition_utils.evaluate_when("is-mng", {"is-mng": False}))

    def test_negated_flag_inverts(self) -> None:
        self.assertFalse(condition_utils.evaluate_when("!is-mng", {"is-mng": True}))
        self.assertTrue(condition_utils.evaluate_when("!is-mng", {"is-mng": False}))

    def test_undefined_flag_raises(self) -> None:
        with self.assertRaises(InvalidInstructionArgumentError):
            condition_utils.evaluate_when("nope", {"is-mng": True})
