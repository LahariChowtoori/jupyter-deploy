"""Static grammar validation for manifest command compositions.

INTENDED FOR UNIT-TEST / CI USE, NOT the runtime manifest-load path. The manifest is a
static template artifact; CI validation (test_manifest_yaml.py + this module's unit tests)
is the fail-fast guarantee — the template ships already-validated. Do NOT wire these
functions into `base_project_handler.retrieve_project_manifest`: the manifest loads on every
`jd` command, a whole-manifest walk buys nothing for a shipped template, may become
non-trivial in Python, and a typo in one command must not block unrelated operations
(e.g. `jd down`). Pydantic field validators still cover the trivially-local rules at load.

Pure functions of the parsed manifest — no project/cluster/IO — so tests call them directly.
"""

import re

from jupyter_deploy.enum import ConditionOperator, InstructionArgumentSource
from jupyter_deploy.exceptions import InvalidCommandGrammarError
from jupyter_deploy.manifest import (
    VOLUME_READINESS_COMMAND,
    JupyterDeployCommandV1,
    JupyterDeployConditionOperandV1,
    JupyterDeployManifestV1,
)

_STEP_REF_RE = re.compile(r"^\[(\d+)\]")


def _step_reference(source_key: str) -> int | None:
    """Return the step index a `source: result` source-key points at, or None if it names no step."""
    match = _STEP_REF_RE.match(source_key)
    return int(match.group(1)) if match else None


def _validate_operand(operand: JupyterDeployConditionOperandV1, ctx: str, violations: list[str]) -> None:
    try:
        source_type = operand.get_source_type()
    except ValueError:
        violations.append(f"{ctx}: unknown operand source '{operand.source}'")
        return

    # Flags are computed once, before the instruction sequence runs (see
    # manifest_command_runner.run_command_sequence), so no instruction result exists yet.
    # A `source: result` flag operand would always resolve against an empty result set and
    # fail. Reject it statically until a use case actually needs sequence-dependent flags.
    if source_type == InstructionArgumentSource.INSTRUCTION_RESULT:
        violations.append(f"{ctx}: flag conditions must not depend on instruction results (source 'result')")
        return

    if source_type == InstructionArgumentSource.LITERAL:
        if operand.value is None:
            violations.append(f"{ctx}: source 'literal' requires 'value'")
    else:
        if not operand.source_key:
            violations.append(f"{ctx}: source '{operand.source}' requires 'source-key'")


def collect_command_violations(command: JupyterDeployCommandV1) -> list[str]:
    """Return the list of grammar violations for a single command (empty if valid)."""
    violations: list[str] = []
    flag_names: set[str] = set()

    for flag in command.flags or []:
        if flag.name in flag_names:
            violations.append(f"command '{command.cmd}': duplicate flag name '{flag.name}'")
        flag_names.add(flag.name)
        if "!" in flag.name:
            violations.append(f"command '{command.cmd}': flag name must not contain '!': '{flag.name}'")

        for idx, condition in enumerate(flag.conditions):
            ctx = f"command '{command.cmd}' flag '{flag.name}' condition[{idx}]"
            try:
                operator = condition.get_operator()
            except ValueError:
                violations.append(f"{ctx}: unknown operator '{condition.operator}'")
                operator = None

            _validate_operand(condition.left, f"{ctx} left", violations)
            _validate_operand(condition.right, f"{ctx} right", violations)

            # `in`'s right operand must be list-typed. A literal scalar can never be a list,
            # so reject it statically. (Output list-ness is only knowable at runtime.)
            if operator == ConditionOperator.IN and condition.right.source.lower() == InstructionArgumentSource.LITERAL:
                violations.append(f"{ctx}: 'in' right operand must be list-typed, not a literal scalar")

    # Step references (`source: result`, `source-key: '[N].Field'`) are POSITIONAL, so inserting a step
    # silently repoints every later reference. Checked here because only the whole command knows the
    # bounds -- a field validator sees one step.
    #
    # What this catches: out-of-bounds (a step was deleted), a step referencing itself or a later step
    # (impossible -- results do not exist yet), and a `source: result` with no '[N]' at all.
    #
    # What it does NOT catch: a reference that shifted to a DIFFERENT but still in-bounds step, which is
    # what inserting a step actually produces. Detecting that needs a static api-name -> result-names
    # registry so the FIELD can be validated against the step, not just the index. Pinned as a known gap
    # in test_manifest_validation.py rather than left as an assumption.
    for idx, instruction in enumerate(command.sequence):
        for arg in instruction.arguments:
            if arg.source.lower() != InstructionArgumentSource.INSTRUCTION_RESULT:
                continue
            ref = _step_reference(arg.source_key)
            ctx = f"command '{command.cmd}' sequence[{idx}] argument '{arg.api_attribute}'"
            if ref is None:
                violations.append(f"{ctx}: source 'result' requires a '[N].Field' source-key")
            elif ref >= idx:
                violations.append(
                    f"{ctx}: references step [{ref}], which is itself or later; a step can only read "
                    "results of steps before it"
                )

    for result in command.results or []:
        if result.source.lower() != InstructionArgumentSource.INSTRUCTION_RESULT:
            continue
        ref = _step_reference(result.source_key)
        ctx = f"command '{command.cmd}' result '{result.result_name}'"
        if ref is None:
            violations.append(f"{ctx}: source 'result' requires a '[N].Field' source-key")
        elif ref >= len(command.sequence):
            violations.append(f"{ctx}: references step [{ref}], but the sequence has {len(command.sequence)} step(s)")

    for idx, instruction in enumerate(command.sequence):
        when = instruction.when
        if when is None:
            continue
        ctx = f"command '{command.cmd}' sequence[{idx}]"
        stripped = when[1:] if when.startswith("!") else when
        if not stripped:
            violations.append(f"{ctx}: when: must reference a non-empty flag name")
            continue
        if "!" in stripped:
            violations.append(f"{ctx}: when: allows at most one leading '!' and no interior '!'")
            continue
        if stripped not in flag_names:
            violations.append(f"{ctx}: when: references undefined flag '{stripped}'")

    return violations


def validate_command(command: JupyterDeployCommandV1) -> None:
    """Raise InvalidCommandGrammarError if the command composition is malformed."""
    violations = collect_command_violations(command)
    if violations:
        raise InvalidCommandGrammarError(violations)


def collect_volume_violations(manifest: JupyterDeployManifestV1) -> list[str]:
    """Violations in the `volumes:` declaration that only the whole manifest can see.

    Omitting the readiness command is a legitimate opt-out for storage where restoring is always safe.
    It is NOT safe in one combination: a volume declaring a `backups-map` is one `jd config
    --restore-volumes` away from being REPLACED from a backup, and without the command nothing
    establishes that the backup still holds what the volume holds. The restore then succeeds and the
    session's work is simply gone -- the failure this whole feature exists to prevent, reintroduced by
    an omission rather than by a bug.
    """
    if not manifest.volumes or manifest.supports_volume_readiness():
        return []

    declaring = [v.name for v in manifest.volumes.static if v.backups_map]
    declaring += [d.group for d in manifest.volumes.dynamic if d.backups_map]
    if not declaring:
        return []

    return [
        f"volumes: {', '.join(declaring)} declare a backups-map, so `jd config --restore-volumes` can "
        f"replace them from a backup, but the template declares no '{VOLUME_READINESS_COMMAND}' command "
        "to establish that the backups are still current"
    ]


def validate_manifest(manifest: JupyterDeployManifestV1) -> None:
    """Raise InvalidCommandGrammarError listing every violation across all commands."""
    violations: list[str] = []
    for command in manifest.commands or []:
        violations.extend(collect_command_violations(command))
    violations.extend(collect_volume_violations(manifest))
    if violations:
        raise InvalidCommandGrammarError(violations)
