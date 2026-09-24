import json
from typing import Any

from pydantic import BaseModel, ConfigDict


class TerraformPlanRootModuleVariable(BaseModel):
    model_config = ConfigDict(extra="allow")
    description: str | None = None
    sensitive: bool | None = False


class TerraformPlanRootModule(BaseModel):
    model_config = ConfigDict(extra="allow")
    variables: dict[str, TerraformPlanRootModuleVariable]


class TerraformPlanConfiguration(BaseModel):
    model_config = ConfigDict(extra="allow")
    root_module: TerraformPlanRootModule


class TerraformPlanVariableContent(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: Any | None


class TerraformPlanChange(BaseModel):
    model_config = ConfigDict(extra="allow")
    actions: list[str] = []


class TerraformPlanResourceChange(BaseModel):
    model_config = ConfigDict(extra="allow")
    change: TerraformPlanChange | None = None


class TerraformPlan(BaseModel):
    model_config = ConfigDict(extra="allow")
    variables: dict[str, TerraformPlanVariableContent]
    configuration: TerraformPlanConfiguration
    resource_changes: list[TerraformPlanResourceChange] = []


def format_terraform_value(value: Any) -> str:
    """Format a value for a .tfvars file."""
    if value is None:
        return "null"
    elif isinstance(value, str):
        # Escape quotes in strings
        escaped_value = value.replace('"', '\\"')
        return f'"{escaped_value}"'
    elif isinstance(value, bool):
        return str(value).lower()
    elif isinstance(value, list):
        if not len(value):
            return "[]"
        out = ["["] + [f"{format_terraform_value(v)}," for v in value] + ["]"]
        return "\n".join(out)
    elif isinstance(value, dict):
        if not len(value):
            return "{}"

        out = ["{"]
        for key, val in value.items():
            # Keys are QUOTED, always. HCL allows a bare key only when it is a valid identifier, so a
            # bare `home` happens to work while `home/external-ebs1` parses as an expression and fails
            # the plan with "Variables not allowed" — a map key is data, not an identifier, and nothing
            # here constrains what a template may use as one.
            out.append(f"{format_terraform_value(str(key))} = {format_terraform_value(val)}")
        out.append("}")
        return "\n".join(out)
    else:
        return str(value)


def extract_plan(plan_content: str) -> TerraformPlan:
    """Parse terraform plan JSON string and return TerraformPlan model.

    Args:
        plan_content: JSON string from `terraform show -json <plan-file>`

    Raises:
        ValueError: If plan_content is not valid JSON or not a dict
        ValidationError: If plan_content doesn't conform to TerraformPlan schema
    """
    try:
        parsed_plan = json.loads(plan_content)
    except json.JSONDecodeError as e:
        raise ValueError("Terraform plan cannot be parsed as JSON.") from e

    if not isinstance(parsed_plan, dict):
        raise ValueError("Terraform plan is not valid: expected a dict.")

    return TerraformPlan(**parsed_plan)


def extract_variables_from_plan(
    plan: TerraformPlan,
) -> tuple[dict[str, TerraformPlanVariableContent], dict[str, TerraformPlanVariableContent]]:
    """Extract variables and secrets from a parsed terraform plan.

    Args:
        plan: Parsed TerraformPlan from extract_plan()

    Returns:
        Tuple of (variables, secrets) dictionaries
    """
    sensitive_varnames = set(
        [var_name for var_name, var_config in plan.configuration.root_module.variables.items() if var_config.sensitive]
    )

    variables = {
        var_name: var_value for var_name, var_value in plan.variables.items() if var_name not in sensitive_varnames
    }
    secrets = {var_name: var_value for var_name, var_value in plan.variables.items() if var_name in sensitive_varnames}

    return variables, secrets


def format_plan_variables(vars: dict[str, TerraformPlanVariableContent]) -> list[str]:
    """Return a list of terraform plan variable entries to save to a .tfvars file."""
    if not vars:
        return []

    out: list[str] = [
        "# do not modify manually: this file is managed by jupyter-deploy\n",
        "# edit variables.yaml instead.\n",
    ]
    out.extend([f"{name} = {format_terraform_value(var.value)}\n" for name, var in vars.items()])
    return out


def format_values_for_dot_tfvars(vars: dict[str, Any]) -> list[str]:
    """Return a list of terraform plan variable entries to save as a .tfvars file."""
    if not vars:
        return []

    out: list[str] = [
        "# do not modify manually: this file is managed by jupyter-deploy\n",
        "# edit variables.yaml instead.\n",
    ]
    out.extend([f"{name} = {format_terraform_value(value)}\n" for name, value in vars.items()])
    return out


def extract_resource_counts_from_plan(plan: TerraformPlan) -> tuple[int, int, int]:
    """Extract resource change counts from a parsed terraform plan.

    Args:
        plan: Parsed TerraformPlan from extract_plan()

    Returns:
        Tuple of (to_add, to_change, to_destroy)
    """
    to_add = 0
    to_change = 0
    to_destroy = 0

    for resource_change in plan.resource_changes:
        if not resource_change.change:
            continue

        actions = resource_change.change.actions

        # Count based on action types
        # Note: replace = delete + create, but we only count as "change"
        if "create" in actions and "delete" not in actions:
            to_add += 1
        elif "delete" in actions and "create" not in actions:
            to_destroy += 1
        elif "update" in actions or ("create" in actions and "delete" in actions):
            to_change += 1

    return to_add, to_change, to_destroy
