"""Tests that README.md and docs pages stay in sync."""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
README = REPO_ROOT / "libs" / "jupyter-deploy-tf-aws-ec2-base" / "README.md"
DOCS_DIR = REPO_ROOT / "docs" / "source" / "templates" / "aws-base-template"

RAW_GITHUB_PREFIX = (
    "https://raw.githubusercontent.com/jupyter-infra/jupyter-deploy/main/docs/source/templates/aws-base-template/"
)


_HEADING_RE = re.compile(r"^(#{3,6}) ")


def _promote_headings(line: str) -> str:
    """Promote headings h3–h6 by one level (### -> ##, #### -> ###, etc.).

    Leaves h1 and h2 unchanged so both README (### under ##) and docs
    (## at top level) normalize to the same depth.
    """
    m = _HEADING_RE.match(line)
    if m:
        return line[1:]
    return line


def _normalize(text: str) -> str:
    """Normalize markdown for comparison.

    - Promote all headings by one level (### -> ##, #### -> ###, etc.)
    - Replace absolute GitHub raw image URLs with relative paths
    - Strip trailing whitespace on each line
    """
    lines = text.strip().splitlines()
    normalized: list[str] = []
    for line in lines:
        line = line.rstrip()
        if "jupyter-deploy.readthedocs.io" in line:
            continue
        line = _promote_headings(line)
        line = line.replace(RAW_GITHUB_PREFIX, "")
        normalized.append(line)
    result = "\n".join(normalized).strip()
    return re.sub(r"\n{3,}", "\n\n", result)


_MD_HEADING_RE = re.compile(r"^(#{1,6}) ")


def _is_heading(line: str, in_fence: bool) -> re.Match[str] | None:
    """Match a markdown heading, ignoring lines inside fenced code blocks."""
    if in_fence:
        return None
    return _MD_HEADING_RE.match(line)


def _extract_section(text: str, heading: str) -> str:
    """Extract a markdown section by heading (including its subsections).

    Returns everything from the heading line to just before the next
    heading at the same or higher level. Ignores # lines inside fenced code blocks.
    """
    lines = text.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    start = None
    in_fence = False
    for i, line in enumerate(lines):
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.strip() == heading and start is None:
            start = i + 1
            continue
        if start is not None:
            m = _is_heading(line, in_fence)
            if m and len(m.group(1)) <= level:
                return "\n".join(lines[start:i])
    if start is not None:
        return "\n".join(lines[start:])
    raise ValueError(f"Heading {heading!r} not found")


def _extract_lines_between(text: str, after: str, before: str) -> str:
    """Extract lines between two heading markers (exclusive)."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if start is None and line.strip() == after:
            start = i + 1
            continue
        if start is not None and line.strip() == before:
            return "\n".join(lines[start:i])
    if start is not None:
        return "\n".join(lines[start:])
    raise ValueError(f"Could not find range between {after!r} and {before!r}")


class TestReadmeDocsConsistency(unittest.TestCase):
    """Verify that duplicated content between README.md and docs pages stays in sync."""

    readme: str
    index_md: str
    prerequisites_md: str
    user_guide_md: str
    architecture_md: str
    details_md: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.readme = README.read_text()
        cls.index_md = (DOCS_DIR / "index.md").read_text()
        cls.prerequisites_md = (DOCS_DIR / "prerequisites.md").read_text()
        cls.user_guide_md = (DOCS_DIR / "user-guide.md").read_text()
        cls.architecture_md = (DOCS_DIR / "architecture.md").read_text()
        cls.details_md = (DOCS_DIR / "details.md").read_text()

    def test_header_description(self) -> None:
        """The opening description paragraph must match between README and index.md."""
        readme_desc = _extract_lines_between(
            self.readme,
            "# Jupyter Deploy AWS EC2 base template",
            "## 10k View",
        )
        index_desc = _extract_lines_between(
            self.index_md,
            "# AWS Base Template",
            "## 10k View",
        )
        self.assertEqual(
            _normalize(readme_desc),
            _normalize(index_desc),
            "Header description drifted between README.md and docs index.md",
        )

    def test_10k_view(self) -> None:
        """The 10k View section must match between README and index.md."""
        readme_section = _extract_lines_between(self.readme, "## 10k View", "## Prerequisites")
        # index.md has a toctree after 10k view — extract up to it
        index_lines = self.index_md.splitlines()
        start = None
        end = len(index_lines)
        for i, line in enumerate(index_lines):
            if line.strip() == "## 10k View":
                start = i + 1
            elif start is not None and (line.strip().startswith("```{toctree}") or line.strip().startswith("## ")):
                end = i
                break
        assert start is not None, "## 10k View not found in index.md"
        index_section = "\n".join(index_lines[start:end])

        self.assertEqual(
            _normalize(readme_section),
            _normalize(index_section),
            "10k View section drifted between README.md and docs index.md",
        )

    def test_prerequisites_section(self) -> None:
        """Prerequisites must match between README and prerequisites.md."""
        readme_section = _extract_section(self.readme, "## Prerequisites")
        doc_lines = self.prerequisites_md.splitlines()
        start = None
        for i, line in enumerate(doc_lines):
            if line.strip() == "# Prerequisites":
                start = i + 1
                break
        assert start is not None, "# Prerequisites not found in prerequisites.md"
        doc_section = "\n".join(doc_lines[start:])
        self.assertEqual(
            _normalize(readme_section),
            _normalize(doc_section),
            "Prerequisites section drifted between README.md and docs prerequisites.md",
        )

    def test_user_guide_section(self) -> None:
        """Usage must match between README and user-guide.md."""
        readme_section = _extract_section(self.readme, "## Usage")
        doc_lines = self.user_guide_md.splitlines()
        start = None
        for i, line in enumerate(doc_lines):
            if line.strip() == "# User Guide":
                start = i + 1
                break
        assert start is not None, "# User Guide not found in user-guide.md"
        doc_section = "\n".join(doc_lines[start:])
        self.assertEqual(
            _normalize(readme_section),
            _normalize(doc_section),
            "User guide section drifted between README.md and docs user-guide.md",
        )

    def test_architecture_section(self) -> None:
        """The Architecture section must match between README and architecture.md."""
        readme_section = _extract_section(self.readme, "## Architecture")
        # architecture.md starts with # Architecture — take everything after that heading
        doc_lines = self.architecture_md.splitlines()
        start = None
        for i, line in enumerate(doc_lines):
            if line.strip() == "# Architecture":
                start = i + 1
                break
        assert start is not None, "# Architecture not found in architecture.md"
        doc_section = "\n".join(doc_lines[start:])

        self.assertEqual(
            _normalize(readme_section),
            _normalize(doc_section),
            "Architecture section drifted between README.md and docs architecture.md",
        )

    def test_details_section(self) -> None:
        """The Details section must match between README and details.md."""
        # README: ## Details up to ## Modules
        readme_section = _extract_lines_between(self.readme, "## Details", "## Modules")
        # details.md: # Details up to ## Terraform Modules
        doc_section = _extract_lines_between(self.details_md, "# Details", "## Terraform Modules")
        self.assertEqual(
            _normalize(readme_section),
            _normalize(doc_section),
            "Details section drifted between README.md and docs details.md",
        )

    def test_license_link(self) -> None:
        """Both README and index.md must reference the MIT License."""
        mit_pattern = re.compile(r"\bMIT License\b", re.IGNORECASE)
        self.assertTrue(
            mit_pattern.search(self.readme),
            "README.md is missing MIT License reference",
        )
        self.assertTrue(
            mit_pattern.search(self.index_md),
            "docs index.md is missing MIT License reference",
        )
