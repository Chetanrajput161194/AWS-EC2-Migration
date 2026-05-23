#!/usr/bin/env python3
"""
EC2 AMI migration automation — discover instances, create AMIs, share cross-account,
and notify via SNS with per-AMI and summary messages.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ami_migration.config import load_config
from ami_migration.logging_setup import setup_logging
from ami_migration.orchestrator import MigrationOrchestrator
from ami_migration.state import StateStore

DEFAULT_CONFIG = Path("config.yaml")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automate EC2 AMI creation and cross-account sharing for migration.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to YAML config (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging on console",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover instances and print plan without making changes",
    )
    return parser.parse_args(argv)


def dry_run(config_path: Path) -> int:
    from ami_migration.discovery import discover_instances

    config = load_config(config_path)
    setup_logging(config.log_file, verbose=True)
    instances = discover_instances(config.region)
    logging.getLogger("ami_migration").info(
        "Dry run: would process %s instance(s) in %s → account %s",
        len(instances),
        config.region,
        config.target_account_id,
    )
    for inst in instances:
        logging.getLogger("ami_migration").info(
            "  - %s | %s | state=%s",
            inst.instance_id,
            inst.name,
            inst.state,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logger = setup_logging(config.log_file, verbose=args.verbose)
    logger.info("Starting EC2 AMI migration (region=%s)", config.region)

    if args.dry_run:
        return dry_run(args.config)

    state = StateStore(config.state_file)
    orchestrator = MigrationOrchestrator(config, state)
    try:
        exit_code = orchestrator.run()
    except KeyboardInterrupt:
        logger.warning("Interrupted — state saved; re-run to resume")
        return 130
    except Exception:
        logger.exception("Migration failed with unhandled error")
        return 1

    if exit_code == 0:
        logger.info("Migration completed successfully")
    else:
        logger.error("Migration finished with one or more failures")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
