#!/usr/bin/env python3
"""Compare object counts (and optionally bytes) between source and destination S3 buckets."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).resolve().parent))
from migration_config import BUCKET_PAIRS, DEFAULT_REGION


@dataclass
class BucketStats:
    count: int = 0
    bytes_total: int = 0


def session_for_profile(region: str, profile: str | None) -> boto3.Session:
    if profile:
        return boto3.Session(profile_name=profile, region_name=region)
    return boto3.Session(region_name=region)


def bucket_stats(s3_client, bucket: str, include_bytes: bool) -> BucketStats:
    stats = BucketStats()
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        contents = page.get("Contents") or []
        stats.count += len(contents)
        if include_bytes:
            stats.bytes_total += sum(obj.get("Size", 0) for obj in contents)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare S3 object counts between source and destination migration buckets."
    )
    parser.add_argument("--region", default=DEFAULT_REGION, help=f"AWS region (default: {DEFAULT_REGION})")
    parser.add_argument("--source-profile", default=None, help="AWS profile for source buckets")
    parser.add_argument("--dest-profile", default=None, help="AWS profile for destination buckets")
    parser.add_argument(
        "--compare-bytes",
        action="store_true",
        help="Also compare total object sizes (slower)",
    )
    args = parser.parse_args()

    config = Config(retries={"mode": "standard", "max_attempts": 10})
    src_session = session_for_profile(args.region, args.source_profile)
    dest_session = session_for_profile(args.region, args.dest_profile)
    src_s3 = src_session.client("s3", config=config)
    dest_s3 = dest_session.client("s3", config=config)

    header = f"{'SOURCE':<22} {'DESTINATION':<22} {'SRC_COUNT':>12} {'DEST_COUNT':>12} {'STATUS':>10}"
    if args.compare_bytes:
        header += f" {'SRC_BYTES':>15} {'DEST_BYTES':>15}"
    print(header)
    print("-" * (len(header) + 5))

    failed = 0
    for source, destination in BUCKET_PAIRS:
        src = bucket_stats(src_s3, source, args.compare_bytes)
        dest = bucket_stats(dest_s3, destination, args.compare_bytes)

        status = "OK"
        if src.count != dest.count:
            status = "MISMATCH"
            failed += 1
        elif args.compare_bytes and src.bytes_total != dest.bytes_total:
            status = "BYTES_DIFF"
            failed += 1

        row = (
            f"{source:<22} {destination:<22} {src.count:>12} {dest.count:>12} {status:>10}"
        )
        if args.compare_bytes:
            row += f" {src.bytes_total:>15} {dest.bytes_total:>15}"
        print(row)

    print()
    if failed == 0:
        print("All bucket object counts match.")
        return 0

    print(f"{failed} bucket pair(s) differ — investigate before considering migration complete.")
    print("Note: equal counts do not guarantee identical keys.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
