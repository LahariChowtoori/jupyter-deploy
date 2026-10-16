"""Tests that the README stays in sync with the template code."""

import re
import unittest
from pathlib import Path

import hcl2  # type: ignore[import-untyped]
from jupyter_deploy.engine.terraform.tf_varfiles import strip_hcl2_quotes

from jupyter_infra_tf_aws_iam_ci.template import TEMPLATE_PATH

README = Path(__file__).resolve().parents[2] / "README.md"


_TABLE_HEADING_RE = re.compile(r"^#{1,6} (.+)$")
_VERSION_TABLE_HEADINGS = ("Requirements", "Providers")


def _declared_constraints(main_tf: Path) -> dict[str, str]:
    """Return the version constraints engine/main.tf declares, under `terraform` for `required_version`."""
    with open(main_tf) as f:
        parsed = strip_hcl2_quotes(hcl2.load(f))
    constraints: dict[str, str] = {}
    for block in parsed.get("terraform", []):
        if "required_version" in block:
            constraints["terraform"] = block["required_version"]
        for providers in block.get("required_providers", []):
            for name, spec in providers.items():
                # python-hcl2 v8 keeps comments under dunder keys such as `__inline_comments__`
                if not name.startswith("__"):
                    constraints[name] = spec["version"]
    return constraints


def _version_tables(text: str) -> dict[str, dict[str, str]]:
    """Return the `| Name | Version |` table under each Requirements/Providers heading, keyed by heading.

    Tables with other columns (e.g. `| Name | Purpose |`) are skipped.
    """
    tables: dict[str, dict[str, str]] = {}
    heading = ""
    rows: dict[str, str] | None = None
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence and (m := _TABLE_HEADING_RE.match(line)):
            heading = m.group(1).strip()
            rows = None
        elif heading in _VERSION_TABLE_HEADINGS and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells == ["Name", "Version"]:
                rows = tables.setdefault(heading, {})
            elif rows is not None and not cells[0].startswith("-"):
                rows[cells[0]] = cells[1]
    return tables


class TestTerraformConstraintsDocs(unittest.TestCase):
    """Verify the documented Terraform and provider versions match what engine/main.tf declares.

    engine/main.tf is the source of truth: a constraint it does not declare must not be documented,
    and every constraint it declares must be.
    """

    def test_version_tables_match_main_tf(self) -> None:
        declared = _declared_constraints(TEMPLATE_PATH / "engine" / "main.tf")
        providers = {name: version for name, version in declared.items() if name != "terraform"}
        for doc in (README,):
            with self.subTest(doc=doc.name):
                tables = _version_tables(doc.read_text())
                self.assertEqual(
                    tables.get("Requirements", {}),
                    declared,
                    f"{doc.name} Requirements table drifted from engine/main.tf",
                )
                self.assertEqual(
                    tables.get("Providers", providers),
                    providers,
                    f"{doc.name} Providers table drifted from engine/main.tf",
                )
