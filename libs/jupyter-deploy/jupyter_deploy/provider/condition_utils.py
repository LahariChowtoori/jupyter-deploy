from jupyter_deploy.engine.outdefs import TemplateOutputDefinition
from jupyter_deploy.enum import ConditionOperator, InstructionArgumentSource
from jupyter_deploy.exceptions import InvalidInstructionArgumentError, OutputNotFoundError
from jupyter_deploy.manifest import JupyterDeployConditionOperandV1, JupyterDeployFlagV1
from jupyter_deploy.provider.resolved_argdefs import (
    ListStrResolvedInstructionArgument,
    ResolvedInstructionArgument,
    StrResolvedInstructionArgument,
    resolve_cliparam_argdef,
    resolve_output_argdef,
    resolve_result_argdef,
)
from jupyter_deploy.provider.resolved_clidefs import ResolvedCliParameter
from jupyter_deploy.provider.resolved_resultdefs import ResolvedInstructionResult

_OPERAND_ARG_NAME = "operand"


def _resolve_operand(
    operand: JupyterDeployConditionOperandV1,
    output_defs: dict[str, TemplateOutputDefinition],
    cli_paramdefs: dict[str, ResolvedCliParameter],
    resolved_resultdefs: dict[str, ResolvedInstructionResult],
) -> ResolvedInstructionArgument:
    """Resolve a condition operand through the same resolvers used for instruction args.

    Raises:
        OutputNotFoundError: when output is missing from template definition.
        InvalidInstructionArgumentError: when operand is not recognized by the CLI.
    """
    source_type = operand.get_source_type()

    if source_type == InstructionArgumentSource.TEMPLATE_OUTPUT:
        try:
            return resolve_output_argdef(outdefs=output_defs, arg_name=_OPERAND_ARG_NAME, source_key=operand.source_key)
        except KeyError:
            raise OutputNotFoundError(operand.source_key) from None
    elif source_type == InstructionArgumentSource.INSTRUCTION_RESULT:
        return resolve_result_argdef(
            resultdefs=resolved_resultdefs, arg_name=_OPERAND_ARG_NAME, source_key=operand.source_key
        )
    elif source_type == InstructionArgumentSource.CLI_ARGUMENT:
        return resolve_cliparam_argdef(
            paramdefs=cli_paramdefs, arg_name=_OPERAND_ARG_NAME, source_key=operand.source_key
        )
    elif source_type == InstructionArgumentSource.LITERAL:
        return StrResolvedInstructionArgument(argument_name=_OPERAND_ARG_NAME, value=operand.value or "")

    raise InvalidInstructionArgumentError(f"Condition operand source is not handled: {source_type}")


def _evaluate_in(
    left: ResolvedInstructionArgument,
    right: ResolvedInstructionArgument,
) -> bool:
    """Evaluate `left in right`, requiring left: str and right: list[str].

    Any other shape fails loud (right not a list — the runtime case the grammar validator
    cannot catch since output list-ness is only known at runtime; left is a list; etc.).
    """
    if not isinstance(left, StrResolvedInstructionArgument):
        raise InvalidInstructionArgumentError(f"'in' condition requires a str left operand, got {type(left).__name__}")
    if not isinstance(right, ListStrResolvedInstructionArgument):
        raise InvalidInstructionArgumentError(
            f"'in' condition requires a list-of-str right operand, got {type(right).__name__}"
        )
    return left.value in right.value


def _evaluate_equals(
    left: ResolvedInstructionArgument,
    right: ResolvedInstructionArgument,
) -> bool:
    """Evaluate `left == right`, requiring both operands to be str.

    Fails loud on any other shape rather than comparing across types: two operands of different
    types are never equal, so a silent False would read as a legitimate branch decision.
    """
    if not isinstance(left, StrResolvedInstructionArgument):
        raise InvalidInstructionArgumentError(
            f"'equals' condition requires a str left operand, got {type(left).__name__}"
        )
    if not isinstance(right, StrResolvedInstructionArgument):
        raise InvalidInstructionArgumentError(
            f"'equals' condition requires a str right operand, got {type(right).__name__}"
        )
    return left.value == right.value


def evaluate_flag(
    flag: JupyterDeployFlagV1,
    output_defs: dict[str, TemplateOutputDefinition],
    cli_paramdefs: dict[str, ResolvedCliParameter],
    resolved_resultdefs: dict[str, ResolvedInstructionResult],
) -> bool:
    """Return the boolean value of a flag: all its conditions ANDed together."""
    for condition in flag.conditions:
        left = _resolve_operand(condition.left, output_defs, cli_paramdefs, resolved_resultdefs)
        right = _resolve_operand(condition.right, output_defs, cli_paramdefs, resolved_resultdefs)
        operator = condition.get_operator()

        if operator == ConditionOperator.IN:
            if not _evaluate_in(left, right):
                return False
        elif operator == ConditionOperator.EQUALS:
            if not _evaluate_equals(left, right):
                return False
        else:
            raise InvalidInstructionArgumentError(f"Unsupported condition operator: {operator}")

    return True


def evaluate_when(when: str, flags: dict[str, bool]) -> bool:
    """Evaluate a step-level `when:` flag-reference against computed flags.

    A leading `!` negates. The referenced flag must exist in `flags`.
    """
    negate = when.startswith("!")
    flag_name = when[1:] if negate else when
    if flag_name not in flags:
        raise InvalidInstructionArgumentError(f"when: references undefined flag '{flag_name}'")
    value = flags[flag_name]
    return not value if negate else value
