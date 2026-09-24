#!/usr/bin/env python3
"""Destroy aws-ec2-jupyterlab E2E deployments that have been up too long.

Usage:
    scripts/reap_stale_jupyterlab.py [--older-than-hours 12] [--project-dir sandbox-e2e] [--dry-run]

The safety net for `e2e-jupyterlab-fresh.yml` leaving a FAILED run's deployment up so a flaky test
can be retried against it. Without this, "keep on failure" means "leak on failure" — and a GPU
instance left mid-flake bills until someone notices.

Three independent conditions have to hold before anything is destroyed, because the failure mode is
tearing down infrastructure somebody is using:

1. **The credentials.** Only deployments visible to the caller are candidates. In CI that is the
   E2E account, which is why a developer's long-lived deployment in their own account is
   untouchable by construction rather than by convention.
2. **The Template tag.** Only `tf-aws-ec2-jupyterlab`. The base and eks-oidc suites, and the
   `sandbox-ci` project itself, all live in the same account under different templates.
3. **Age.** Default 12h, measured from the instance's `LaunchTime`. An E2E run is capped at
   `timeout-minutes: 120`, so a threshold this far above it cannot catch a run in flight.

Age comes from EC2 rather than the S3 store because `jd projects list` reports ids only, with no
timestamps. A consequence worth knowing: a deployment whose apply failed BEFORE the instance was
created leaves volumes and security groups but no instance, so it is invisible here. Those are cheap
and rare; `find_takedown_jupyterlab.py` with no `--deployment-id` is the nuclear option for them.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime

TEMPLATE_TAG = "tf-aws-ec2-jupyterlab"

# `terminated` and `shutting-down` are excluded: they bill nothing and are already on their way out.
LIVE_STATES = "running,stopped,pending,stopping"


def deployment_creation_ages(now: datetime, deployment_ids: set[str], region: str | None = None) -> dict[str, float]:
    """Return deployment_id -> age in hours, from the SSM documents each deployment creates.

    `LaunchTime` is the wrong clock: EC2 resets it when an instance starts, and the zone swap replaces
    the instance outright, so a run that fails at or after the swap presents a minutes-old instance and
    resets the very threshold this reaper trips on -- exactly the keep-on-failure case it exists for.

    SSM documents are the anchor instead. They are created once at apply, are not AZ-bound, and update
    in place rather than being replaced when their content changes, so the CreatedDate survives
    everything the instance does not. Their names end in the deployment id and their prefixes are
    hardcoded in the template, unlike the IAM role or bucket prefixes, which are variables a deployment
    can change -- a name-prefix match would silently stop matching and fall back to the weaker floor.

    One tag-filtered call, and `ssm:ListDocuments` rather than account-wide IAM or S3 enumeration.

    Best-effort: on any failure the age falls back to `LaunchTime`, which understates rather than fails.
    """
    if not deployment_ids:
        return {}

    cmd = [
        "aws",
        "ssm",
        "list-documents",
        "--filters",
        "Key=Owner,Values=Self",
        f"Key=tag:Template,Values={TEMPLATE_TAG}",
        "--query",
        "DocumentIdentifiers[].[Name,CreatedDate]",
        "--output",
        "json",
    ]
    if region:
        cmd += ["--region", region]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"  ! could not read SSM documents, falling back to instance launch times: {result.stderr.strip()}")
        return {}

    ages: dict[str, float] = {}
    for name, created_date in json.loads(result.stdout or "[]"):
        # `<hardcoded-prefix>-<deployment_id>`; ignore anything whose id we did not ask about.
        deployment_id = name.rsplit("-", 1)[-1]
        if deployment_id not in deployment_ids:
            continue
        age_hours = (now - datetime.fromisoformat(created_date)).total_seconds() / 3600
        ages[deployment_id] = max(ages.get(deployment_id, 0.0), age_hours)
    return ages


def find_stale_deployments(older_than_hours: float, region: str | None = None) -> list[tuple[str, float]]:
    """Return (deployment_id, age_hours) for every jupyterlab instance older than the threshold."""
    cmd = [
        "aws",
        "ec2",
        "describe-instances",
        "--filters",
        f"Name=tag:Template,Values={TEMPLATE_TAG}",
        f"Name=instance-state-name,Values={LIVE_STATES}",
        "--query",
        "Reservations[].Instances[].{LaunchTime:LaunchTime,DeploymentId:Tags[?Key=='DeploymentId']|[0].Value}",
        "--output",
        "json",
    ]
    if region:
        cmd += ["--region", region]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    instances = json.loads(result.stdout or "[]")

    now = datetime.now(UTC)
    created_ages = deployment_creation_ages(
        now,
        {i["DeploymentId"] for i in instances if i.get("DeploymentId") and i.get("LaunchTime")},
        region=region,
    )
    stale: dict[str, float] = {}

    for instance in instances:
        deployment_id = instance.get("DeploymentId")
        launch_time = instance.get("LaunchTime")
        if not deployment_id or not launch_time:
            # An instance carrying the Template tag but no DeploymentId cannot be reaped safely:
            # the takedown path is keyed on that id, so there is nothing to hand it.
            print(f"  ! skipping an instance with no DeploymentId tag (launched {launch_time})")
            continue

        # LaunchTime is the FLOOR, not the age: EC2 resets it when an instance starts, and a zone swap
        # replaces the instance outright, so a run that fails at or after the swap would present a
        # minutes-old instance and reset the very clock this reaper trips on -- which is precisely the
        # keep-on-failure scenario it exists for. The deployment's SSM documents are created once and survive
        # both, so the apply-time anchor wins when it is older.
        age_hours = (now - datetime.fromisoformat(launch_time)).total_seconds() / 3600
        stale[deployment_id] = max(age_hours, created_ages.get(deployment_id, 0.0))

    return sorted(
        ((deployment_id, age) for deployment_id, age in stale.items() if age > older_than_hours),
        key=lambda item: item[1],
        reverse=True,
    )


def reap(deployment_id: str, project_dir: str) -> bool:
    """Take one deployment down. Returns True on success."""
    print(f"\n=== reaping {deployment_id} ===", flush=True)
    result = subprocess.run(
        ["just", "find-takedown-jupyterlab", project_dir, deployment_id],
        check=False,
    )
    if result.returncode != 0:
        print(f"  FAILED to reap {deployment_id} (exit {result.returncode})", file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--older-than-hours",
        type=float,
        default=12.0,
        help="Reap deployments whose instance launched more than this many hours ago (default: 12).",
    )
    parser.add_argument(
        "--project-dir",
        default="sandbox-e2e",
        help="Local directory to restore each project into before destroying it.",
    )
    parser.add_argument("--region", default=None, help="AWS region (defaults to the environment).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be reaped and exit 0 without destroying anything.",
    )
    args = parser.parse_args()

    stale = find_stale_deployments(args.older_than_hours, region=args.region)

    if not stale:
        print(f"No {TEMPLATE_TAG} deployment older than {args.older_than_hours}h. Nothing to reap.")
        return 0

    print(f"Found {len(stale)} stale deployment(s) older than {args.older_than_hours}h:")
    for deployment_id, age_hours in stale:
        print(f"  {deployment_id}  ({age_hours:.1f}h old)")

    if args.dry_run:
        print("\n--dry-run: nothing destroyed.")
        return 0

    # Every deployment is attempted even if an earlier one fails: one wedged teardown must not
    # shelter the rest, since the whole point is that nobody is watching.
    failed = [deployment_id for deployment_id, _ in stale if not reap(deployment_id, args.project_dir)]

    print(f"\nReaped {len(stale) - len(failed)}/{len(stale)} deployment(s).")
    if failed:
        print(f"Could not reap: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
