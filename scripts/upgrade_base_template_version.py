#!/usr/bin/env python3
"""
Script to upgrade version information across all files in the jupyter-deploy-tf-aws-ec2-base template.

Usage:
    python scripts/upgrade_base_template_version.py NEW_VERSION
"""

import argparse
import sys
from pathlib import Path

from version_edit import set_init_version, set_manifest_version, set_tf_template_version, set_toml_version


def main() -> None:
    parser = argparse.ArgumentParser(description="Update version across all project files.")
    parser.add_argument("new_version", help="New version string (e.g., '0.2.1')")

    args = parser.parse_args()
    new_version = args.new_version

    project_path = Path(__file__).parent.parent / "libs" / "jupyter-deploy-tf-aws-ec2-base"

    if not project_path.exists():
        print(f"Error: Project path {project_path} not found")
        sys.exit(1)

    print(f"Updating all version references to: {new_version}\n")

    package_path = project_path / "jupyter_deploy_tf_aws_ec2_base"
    template_path = package_path / "template"
    services_path = template_path / "services"

    set_toml_version(project_path / "pyproject.toml", new_version)
    set_init_version(package_path / "__init__.py", new_version)
    set_manifest_version(template_path / "manifest.yaml", new_version)
    set_tf_template_version(template_path / "engine" / "main.tf", new_version)

    # Jupyter service files, one per package manager flavor
    set_toml_version(services_path / "jupyter" / "pyproject.jupyter.toml", new_version)
    set_toml_version(services_path / "jupyter-pixi" / "pixi.jupyter.toml", new_version)

    # Kernel files
    set_toml_version(services_path / "jupyter" / "pyproject.kernel.toml", new_version)
    set_toml_version(services_path / "jupyter-pixi" / "pyproject.kernel.toml", new_version)

    print("\nVersion update completed successfully!")


if __name__ == "__main__":
    main()
