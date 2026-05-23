#!/usr/bin/env python3
"""Set TransferMode=CHANGED on all DataSync tasks created by datasync-tasks CloudFormation."""

from __future__ import annotations

import argparse
import sys
from enum import Enum
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from migration_config import (
    CFN_TASK_NAMES,
    DATASYNC_TASK_OPTIONS,
    DEFAULT_REGION,
    DEFAULT_STACK_NAME,
)


class UpdateResult(Enum):
    UPDATED = "updated"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"
    FAILED = "failed"


def session_for_profile(region: str, profile: str | None) -> boto3.Session:
    if profile:
        return boto3.Session(profile_name=profile, region_name=region)
    return boto3.Session(region_name=region)


def task_arns_from_stack(cfn_client, stack_name: str) -> list[str]:
    response = cfn_client.describe_stacks(StackName=stack_name)
    outputs = response["Stacks"][0].get("Outputs") or []
    for output in outputs:
        if output.get("OutputKey") == "TaskArns":
            value = (output.get("OutputValue") or "").strip()
            if value:
                return [arn.strip() for arn in value.split(",") if arn.strip()]
    return []


def task_arn_by_name(datasync_client, name: str) -> str | None:
    paginator = datasync_client.get_paginator("list_tasks")
    for page in paginator.paginate():
        for task in page.get("Tasks") or []:
            if task.get("Name") == name:
                return task.get("TaskArn")
    return None


def collect_task_arns(
    cfn_client,
    datasync_client,
    stack_name: str,
    task_arns_arg: list[str] | None,
) -> list[str]:
    if task_arns_arg:
        return task_arns_arg

    arns = task_arns_from_stack(cfn_client, stack_name)
    if arns:
        return arns

    print("Stack output not found; resolving tasks by name...", file=sys.stderr)
    resolved: list[str] = []
    for name in CFN_TASK_NAMES:
        arn = task_arn_by_name(datasync_client, name)
        if not arn:
            print(f"Warning: task not found: {name}", file=sys.stderr)
            continue
        resolved.append(arn)
    return resolved


def update_task(datasync_client, task_arn: str, dry_run: bool) -> UpdateResult:
    desc = datasync_client.describe_task(TaskArn=task_arn)
    name = desc.get("Name", task_arn)
    options = desc.get("Options") or {}
    mode = options.get("TransferMode", "")

    if mode == "CHANGED":
        print(f"SKIP  {name}  (already CHANGED)")
        return UpdateResult.SKIPPED

    print(f"UPDATE {name}  {mode} -> CHANGED")
    if dry_run:
        return UpdateResult.DRY_RUN

    datasync_client.update_task(TaskArn=task_arn, Options=DATASYNC_TASK_OPTIONS)
    return UpdateResult.UPDATED


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set DataSync tasks to TransferMode=CHANGED (changed data only)."
    )
    parser.add_argument("--region", default=DEFAULT_REGION, help=f"AWS region (default: {DEFAULT_REGION})")
    parser.add_argument("--profile", default=None, help="AWS profile for API calls")
    parser.add_argument(
        "--stack-name",
        default=DEFAULT_STACK_NAME,
        help=f"CloudFormation stack name (default: {DEFAULT_STACK_NAME})",
    )
    parser.add_argument(
        "--task-arn",
        action="append",
        dest="task_arns",
        metavar="ARN",
        help="Task ARN (repeatable); skips stack lookup if set",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without calling update-task",
    )
    args = parser.parse_args()

    session = session_for_profile(args.region, args.profile)
    cfn = session.client("cloudformation")
    datasync = session.client("datasync")

    try:
        arns = collect_task_arns(cfn, datasync, args.stack_name, args.task_arns)
    except ClientError as exc:
        print(f"Error resolving tasks: {exc}", file=sys.stderr)
        return 1

    if not arns:
        print("Error: no task ARNs found.", file=sys.stderr)
        return 1

    print(f"Region: {args.region}")
    print(f"Tasks to process: {len(arns)}")
    if args.dry_run:
        print("DRY_RUN enabled — no changes will be applied.")
    print()

    counts = {r: 0 for r in UpdateResult}
    for arn in arns:
        try:
            result = update_task(datasync, arn, args.dry_run)
            counts[result] += 1
        except ClientError as exc:
            print(f"FAILED {arn}: {exc}", file=sys.stderr)
            counts[UpdateResult.FAILED] += 1

    print()
    if args.dry_run:
        print(
            f"Done. Would update: {counts[UpdateResult.DRY_RUN]}, "
            f"already CHANGED: {counts[UpdateResult.SKIPPED]}, "
            f"failed: {counts[UpdateResult.FAILED]}"
        )
    else:
        print(
            f"Done. Updated: {counts[UpdateResult.UPDATED]}, "
            f"skipped (already CHANGED): {counts[UpdateResult.SKIPPED]}, "
            f"failed: {counts[UpdateResult.FAILED]}"
        )

    return 1 if counts[UpdateResult.FAILED] else 0


if __name__ == "__main__":
    sys.exit(main())
