"""SNS notifications for AMI migration events."""

from __future__ import annotations

import json
import logging

import boto3
from botocore.config import Config

from ami_migration.config import MigrationConfig
from ami_migration.retry import with_retry
from ami_migration.state import InstanceRecord, InstanceStatus

logger = logging.getLogger("ami_migration")


def _sns_client(region: str):
    return boto3.client(
        "sns",
        region_name=region,
        config=Config(retries={"mode": "standard", "max_attempts": 10}),
    )


def _publish(config: MigrationConfig, subject: str, payload: dict) -> None:
    client = _sns_client(config.region)
    message = json.dumps(payload, indent=2, default=str)

    def _send() -> None:
        client.publish(
            TopicArn=config.sns_topic_arn,
            Subject=subject[:100],
            Message=message,
        )

    with_retry(_send, operation="sns.publish")
    logger.info("SNS notification sent: %s", subject)


def notify_ami_event(config: MigrationConfig, record: InstanceRecord) -> None:
    status_label = (
        "success"
        if record.status == InstanceStatus.COMPLETED
        else "failure"
        if record.status == InstanceStatus.FAILED
        else record.status.value
    )
    payload = {
        "event_type": "ami_migration_instance",
        "instance_name": record.instance_name,
        "instance_id": record.instance_id,
        "ami_id": record.ami_id,
        "ami_name": record.ami_name,
        "status": status_label,
        "detail_status": record.status.value,
        "failure_reason": record.failure_reason,
        "snapshot_ids": record.snapshot_ids,
        "target_account_id": config.target_account_id,
        "region": config.region,
    }
    subject = (
        f"[AMI Migration] {status_label.upper()}: "
        f"{record.instance_name} ({record.instance_id})"
    )
    _publish(config, subject, payload)


def notify_summary(
    config: MigrationConfig,
    records: list[InstanceRecord],
    progress: dict[str, int],
) -> None:
    instances = [
        {
            "instance_name": r.instance_name,
            "instance_id": r.instance_id,
            "ami_id": r.ami_id,
            "status": r.status.value,
            "failure_reason": r.failure_reason,
        }
        for r in records
    ]
    payload = {
        "event_type": "ami_migration_summary",
        "status": "completed",
        "region": config.region,
        "target_account_id": config.target_account_id,
        "progress": progress,
        "instances": instances,
    }
    subject = (
        f"[AMI Migration] SUMMARY: {progress.get('completed', 0)}/{progress.get('total', 0)} "
        f"completed, {progress.get('failed', 0)} failed"
    )
    _publish(config, subject, payload)
