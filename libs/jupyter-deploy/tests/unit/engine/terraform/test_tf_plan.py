# mypy: disable-error-code=attr-defined

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from jupyter_deploy.engine.terraform.tf_plan import (
    TerraformPlan,
    TerraformPlanVariableContent,
    extract_plan,
    extract_resource_counts_from_plan,
    extract_variables_from_plan,
    format_plan_variables,
    format_terraform_value,
    format_values_for_dot_tfvars,
)


class TestFormatTerraformValue(unittest.TestCase):
    def test_null_value(self) -> None:
        self.assertEqual(format_terraform_value(None), "null")

    def test_str_value(self) -> None:
        self.assertEqual(format_terraform_value("hello"), '"hello"')

    def test_empty_str_value(self) -> None:
        self.assertEqual(format_terraform_value(""), '""')

    def test_bool_true_value(self) -> None:
        self.assertEqual(format_terraform_value(True), "true")

    def test_bool_false_value(self) -> None:
        self.assertEqual(format_terraform_value(False), "false")

    def test_list_str_value(self) -> None:
        self.assertEqual(format_terraform_value(["a", "b"]), '[\n"a",\n"b",\n]')

    def test_list_int_value(self) -> None:
        self.assertEqual(format_terraform_value([1, 2]), "[\n1,\n2,\n]")

    def test_list_float_value(self) -> None:
        self.assertEqual(format_terraform_value([1.1, 2.2]), "[\n1.1,\n2.2,\n]")

    def test_empty_list_value(self) -> None:
        self.assertEqual(format_terraform_value([]), "[]")

    def test_dict_str_str_value(self) -> None:
        result = format_terraform_value({"key": "value"})
        self.assertEqual(result, '{\n"key" = "value"\n}')

    def test_dict_str_int_value(self) -> None:
        result = format_terraform_value({"key": 123})
        self.assertEqual(result, '{\n"key" = 123\n}')

    def test_dict_str_float_value(self) -> None:
        result = format_terraform_value({"key": 1.23})
        self.assertEqual(result, '{\n"key" = 1.23\n}')

    def test_dict_key_needing_quotes(self) -> None:
        """The regression: a bare `home/external-ebs1` parses as an expression, not a key.

        Terraform fails the plan with "Variables not allowed" — so an unquoted key is not merely
        untidy, it makes `jd config --restore-volumes` impossible for any volume below the top level.
        """
        result = format_terraform_value({"home": "snap-1", "home/external-ebs1": "snap-2"})
        self.assertEqual(result, '{\n"home" = "snap-1"\n"home/external-ebs1" = "snap-2"\n}')

    def test_dict_key_with_a_quote_is_escaped(self) -> None:
        result = format_terraform_value({'we"ird': "v"})
        self.assertEqual(result, '{\n"we\\"ird" = "v"\n}')

    def test_dict_key_with_a_dot_is_quoted(self) -> None:
        """A dotted key is not a valid bare identifier either — terraform reads it as an attribute."""
        result = format_terraform_value({"a.b": "v"})
        self.assertEqual(result, '{\n"a.b" = "v"\n}')

    def test_dict_key_with_a_dash_is_quoted(self) -> None:
        """`home-data` would parse as a subtraction."""
        result = format_terraform_value({"home-data": "v"})
        self.assertEqual(result, '{\n"home-data" = "v"\n}')

    def test_non_string_dict_key_is_stringified_then_quoted(self) -> None:
        """A YAML map can yield int keys; HCL keys are strings, so they must be rendered as such."""
        result = format_terraform_value({1: "v"})
        self.assertEqual(result, '{\n"1" = "v"\n}')

    def test_nested_dict_keys_are_quoted_too(self) -> None:
        result = format_terraform_value({"outer/a": {"inner/b": "v"}})
        self.assertEqual(result, '{\n"outer/a" = {\n"inner/b" = "v"\n}\n}')

    def test_dict_of_lists_quotes_the_keys(self) -> None:
        result = format_terraform_value({"home/data": ["a"]})
        self.assertEqual(result, '{\n"home/data" = [\n"a",\n]\n}')

    def test_a_quoted_key_round_trips_through_terraform_parsing(self) -> None:
        """The regression in one line: an unquoted `home/x` made terraform fail the whole plan with
        `Error: Variables not allowed`, so `jd config --restore-volumes` could never restore a
        non-top-level volume. Quoting is what makes the emitted tfvars parseable at all.
        """
        rendered = format_terraform_value({"home": "snap-1", "home/external-ebs1": "snap-2"})
        for key in ('"home"', '"home/external-ebs1"'):
            self.assertIn(f"{key} = ", rendered)
        self.assertNotIn("\nhome = ", rendered)
        self.assertNotIn("\nhome/external-ebs1 = ", rendered)

    def test_empty_dict_value(self) -> None:
        self.assertEqual(format_terraform_value({}), "{}")


class TestExtractVariablesFromPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        mock_plan_path = Path(__file__).parent / "mock_plan.json"
        with open(mock_plan_path) as f:
            cls.plan_content = f.read()
        cls.plan = TerraformPlan(**json.loads(cls.plan_content))

    def test_happy_case(self) -> None:
        cls = self.__class__
        plan = extract_plan(cls.plan_content)
        result = extract_variables_from_plan(plan)

        expect_vars = {k: v for k, v in cls.plan.variables.items() if "secret" not in k}
        expect_secrets = {k: v for k, v in cls.plan.variables.items() if "secret" in k}

        self.assertTupleEqual(result, (expect_vars, expect_secrets))

    def test_invalid_json_raise_value_error(self) -> None:
        cls = self.__class__

        with self.assertRaises(ValueError):
            extract_plan(cls.plan_content[:-2])

    def test_non_dict_json_raise_value_error(self) -> None:
        with self.assertRaises(ValueError):
            extract_plan(json.dumps(["I should be a dict"]))

    def test_no_variables_key_raise_pydantic_validation_error(self) -> None:
        cls = self.__class__
        no_variables_plan = json.loads(cls.plan_content)
        del no_variables_plan["variables"]

        with self.assertRaises(ValidationError):
            extract_plan(json.dumps(no_variables_plan))

    def test_no_configuration_key_raise_pydantic_validation_error(self) -> None:
        cls = self.__class__
        no_config_plan = json.loads(cls.plan_content)
        del no_config_plan["configuration"]

        with self.assertRaises(ValidationError):
            extract_plan(json.dumps(no_config_plan))

    def test_no_config_root_variables_key_raise_pydantic_validation_error(self) -> None:
        cls = self.__class__
        modified_plan = json.loads(cls.plan_content)
        del modified_plan["configuration"]["root_module"]["variables"]

        with self.assertRaises(ValidationError):
            extract_plan(json.dumps(modified_plan))


class TestFormatPlanVariables(unittest.TestCase):
    def test_happy_case(self) -> None:
        vars = {
            "var_str": TerraformPlanVariableContent(value="value1"),
            "var_int": TerraformPlanVariableContent(value=123),
            "var_float": TerraformPlanVariableContent(value=3.1459),
            "var_bool": TerraformPlanVariableContent(value=True),
            "var_null": TerraformPlanVariableContent(value=None),
            "var_empty_dict": TerraformPlanVariableContent(value={}),
            "var_dict": TerraformPlanVariableContent(value={"key1": "val1", "key2": "val2"}),
            "var_dict_int": TerraformPlanVariableContent(value={"key1": 10, "key2": 11}),
            "var_empty_list": TerraformPlanVariableContent(value=[]),
            "var_list": TerraformPlanVariableContent(value=["a", "b"]),
        }
        result = format_plan_variables(vars)
        self.assertGreaterEqual(len(result), len(vars.keys()))  # allow for top-level comments
        self.assertIn('var_str = "value1"\n', result)
        self.assertIn("var_int = 123\n", result)
        self.assertIn("var_float = 3.1459\n", result)
        self.assertIn("var_bool = true\n", result)
        self.assertIn("var_null = null\n", result)
        self.assertIn("var_empty_dict = {}\n", result)
        self.assertIn('var_dict = {\n"key1" = "val1"\n"key2" = "val2"\n}\n', result)
        self.assertIn('var_dict_int = {\n"key1" = 10\n"key2" = 11\n}\n', result)
        self.assertIn("var_empty_list = []\n", result)
        self.assertIn('var_list = [\n"a",\n"b",\n]\n', result)

    def test_empty_vars_return_empty_list(self) -> None:
        vars: dict[str, TerraformPlanVariableContent] = {}
        result = format_plan_variables(vars)
        self.assertEqual(result, [])


class TestFormatValuesForDotTfvars(unittest.TestCase):
    def test_happy_case(self) -> None:
        vars = {
            "var_str": "value1",
            "var_int": 123,
            "var_float": 3.1459,
            "var_bool": True,
            "var_null": None,
            "var_empty_dict": {},
            "var_dict": {"key1": "val1", "key2": "val2"},
            "var_dict_int": {"key1": 10, "key2": 11},
            "var_empty_list": [],
            "var_list": ["a", "b"],
        }
        result = format_values_for_dot_tfvars(vars)
        self.assertGreaterEqual(len(result), len(vars.keys()))  # allow for include comments
        self.assertIn('var_str = "value1"\n', result)
        self.assertIn("var_int = 123\n", result)
        self.assertIn("var_float = 3.1459\n", result)
        self.assertIn("var_bool = true\n", result)
        self.assertIn("var_null = null\n", result)
        self.assertIn("var_empty_dict = {}\n", result)
        self.assertIn('var_dict = {\n"key1" = "val1"\n"key2" = "val2"\n}\n', result)
        self.assertIn('var_dict_int = {\n"key1" = 10\n"key2" = 11\n}\n', result)
        self.assertIn("var_empty_list = []\n", result)
        self.assertIn('var_list = [\n"a",\n"b",\n]\n', result)

    def test_empty_vars_return_empty_list(self) -> None:
        vars: dict[str, TerraformPlanVariableContent] = {}
        result = format_values_for_dot_tfvars(vars)
        self.assertEqual(result, [])


class TestExtractResourceCountsFromJsonPlan(unittest.TestCase):
    """Test cases for extract_resource_counts_from_plan function."""

    def _make_plan(self, resource_changes: list[dict]) -> str:
        """Helper to create minimal valid terraform plan JSON."""
        return json.dumps(
            {
                "variables": {},
                "configuration": {"root_module": {"variables": {}}},
                "resource_changes": resource_changes,
            }
        )

    def test_extracts_create_actions(self) -> None:
        """Test extraction of resources to be created."""
        plan = extract_plan(
            self._make_plan(
                [
                    {"change": {"actions": ["create"]}},
                    {"change": {"actions": ["create"]}},
                    {"change": {"actions": ["create"]}},
                ]
            )
        )
        to_add, to_change, to_destroy = extract_resource_counts_from_plan(plan)
        self.assertEqual(to_add, 3)
        self.assertEqual(to_change, 0)
        self.assertEqual(to_destroy, 0)

    def test_extracts_delete_actions(self) -> None:
        """Test extraction of resources to be deleted."""
        plan = extract_plan(
            self._make_plan(
                [
                    {"change": {"actions": ["delete"]}},
                    {"change": {"actions": ["delete"]}},
                ]
            )
        )
        to_add, to_change, to_destroy = extract_resource_counts_from_plan(plan)
        self.assertEqual(to_add, 0)
        self.assertEqual(to_change, 0)
        self.assertEqual(to_destroy, 2)

    def test_extracts_update_actions(self) -> None:
        """Test extraction of resources to be updated."""
        plan = extract_plan(
            self._make_plan(
                [
                    {"change": {"actions": ["update"]}},
                    {"change": {"actions": ["update"]}},
                    {"change": {"actions": ["update"]}},
                ]
            )
        )
        to_add, to_change, to_destroy = extract_resource_counts_from_plan(plan)
        self.assertEqual(to_add, 0)
        self.assertEqual(to_change, 3)
        self.assertEqual(to_destroy, 0)

    def test_extracts_replace_actions(self) -> None:
        """Test extraction of resources to be replaced (delete + create)."""
        plan = extract_plan(
            self._make_plan(
                [
                    {"change": {"actions": ["delete", "create"]}},
                    {"change": {"actions": ["create", "delete"]}},
                ]
            )
        )
        to_add, to_change, to_destroy = extract_resource_counts_from_plan(plan)
        self.assertEqual(to_add, 0)
        self.assertEqual(to_change, 2)
        self.assertEqual(to_destroy, 0)

    def test_extracts_mixed_actions(self) -> None:
        """Test extraction with mixed action types."""
        plan = extract_plan(
            self._make_plan(
                [
                    {"change": {"actions": ["create"]}},
                    {"change": {"actions": ["create"]}},
                    {"change": {"actions": ["update"]}},
                    {"change": {"actions": ["delete"]}},
                    {"change": {"actions": ["delete", "create"]}},
                ]
            )
        )
        to_add, to_change, to_destroy = extract_resource_counts_from_plan(plan)
        self.assertEqual(to_add, 2)
        self.assertEqual(to_change, 2)
        self.assertEqual(to_destroy, 1)

    def test_handles_empty_resource_changes(self) -> None:
        """Test handling of plans with no resource changes."""
        plan = extract_plan(self._make_plan([]))
        to_add, to_change, to_destroy = extract_resource_counts_from_plan(plan)
        self.assertEqual(to_add, 0)
        self.assertEqual(to_change, 0)
        self.assertEqual(to_destroy, 0)

    def test_handles_no_op_actions(self) -> None:
        """Test that no-op actions are ignored."""
        plan = extract_plan(
            self._make_plan(
                [
                    {"change": {"actions": ["no-op"]}},
                    {"change": {"actions": ["no-op"]}},
                    {"change": {"actions": ["create"]}},
                ]
            )
        )
        to_add, to_change, to_destroy = extract_resource_counts_from_plan(plan)
        self.assertEqual(to_add, 1)
        self.assertEqual(to_change, 0)
        self.assertEqual(to_destroy, 0)

    def test_skips_resources_with_no_change(self) -> None:
        """Test that resources without change attribute are skipped."""
        plan = extract_plan(
            self._make_plan(
                [
                    {},  # No change attribute
                    {"change": None},  # Null change
                    {"change": {"actions": ["create"]}},
                    {"change": {"actions": ["delete"]}},
                ]
            )
        )
        to_add, to_change, to_destroy = extract_resource_counts_from_plan(plan)
        self.assertEqual(to_add, 1)
        self.assertEqual(to_change, 0)
        self.assertEqual(to_destroy, 1)

    def test_raises_value_error_on_invalid_json(self) -> None:
        """Test that invalid JSON raises ValueError."""
        with self.assertRaises(ValueError) as context:
            extract_plan("not valid json")
        self.assertIn("cannot be parsed as JSON", str(context.exception))

    def test_raises_value_error_on_non_dict(self) -> None:
        """Test that non-dict JSON raises ValueError."""
        with self.assertRaises(ValueError) as context:
            extract_plan(json.dumps([1, 2, 3]))
        self.assertIn("expected a dict", str(context.exception))

    def test_handles_missing_resource_changes_key(self) -> None:
        """Test handling of plans without resource_changes key."""
        plan = extract_plan(json.dumps({"variables": {}, "configuration": {"root_module": {"variables": {}}}}))
        to_add, to_change, to_destroy = extract_resource_counts_from_plan(plan)
        self.assertEqual(to_add, 0)
        self.assertEqual(to_change, 0)
        self.assertEqual(to_destroy, 0)

    def test_raises_validation_error_on_malformed_resource_changes(self) -> None:
        """Test that malformed resource changes raise ValidationError during parsing."""
        with self.assertRaises(ValidationError):
            extract_plan(
                self._make_plan(
                    [
                        "not a dict",  # type: ignore
                        {"change": "not a dict"},
                        {"change": {"actions": "not a list"}},  # type: ignore
                        {"change": {"actions": ["create"]}},
                    ]
                )
            )
