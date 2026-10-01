"""Undeployed project utilities for testing CLI behavior without deployment."""

import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from pytest_jupyter_deploy.cli import JDCli
from pytest_jupyter_deploy.suite_config import SuiteConfig


@contextmanager
def undeployed_project(suite_config: SuiteConfig) -> Generator[tuple[Path, JDCli]]:
    """Create a temporary undeployed project for testing.

    This context manager:
    1. Creates a temporary directory in /tmp
    2. Runs `jd init` to initialize the project
    3. Does NOT run `jd config` or `jd up`
    4. Yields the project path and CLI instance
    5. Cleans up the temporary directory on exit

    Args:
        suite_config: Suite configuration to get template details

    Yields:
        Tuple of (project_path, cli_instance)

    Example:
        with undeployed_project(suite_config) as (project_path, cli):
            result = cli.run_command(["jupyter-deploy", "open"])
            assert "URL not available" in result.stdout
    """
    # Ensure suite_config is loaded to access template details
    suite_config.load()

    # Create temporary directory
    temp_dir = Path(tempfile.mkdtemp(prefix="jd-e2e-mock-"))

    try:
        # Initialize CLI with temp directory
        cli = JDCli(temp_dir)

        # Run jd init
        engine = suite_config.template_engine.value
        provider = suite_config.template_provider
        infrastructure = suite_config.template_infrastructure
        base_name = suite_config.template_base_name

        init_cmd = [
            "jupyter-deploy",
            "init",
            "--engine",
            engine,
            "--provider",
            provider,
            "--infrastructure",
            infrastructure,
            "--template",
            base_name,
            ".",
        ]
        cli.run_command(init_cmd)

        # Yield the project path and CLI for testing
        yield temp_dir, cli

    finally:
        # Clean up temporary directory
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
