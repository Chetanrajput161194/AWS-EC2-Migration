"""AMI creation, monitoring, and cross-account sharing."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import boto3
from botocore.config import Config

from ami_migration.config import MigrationConfig
from ami_migration.retry import with_retry
from ami_migration.state import InstanceRecord, InstanceStatus

logger = logging.getLogger("ami_migration")

AMI_NAME_MAX_LEN = 128


def sanitize_ami_component(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\-_().,/ ]", "-", value.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned.strip("-") or "unnamed"


def build_ami_name(config: MigrationConfig, instance_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = f"migration-{sanitize_ami_component(instance_name)}-{timestamp}"
    name = f"{config.ami_name_prefix}{base}" if config.ami_name_prefix else base
    return name[:AMI_NAME_MAX_LEN]


def _ec2_client(region: str):
    return boto3.client(
        "ec2",
        region_name=region,
        config=Config(retries={"mode": "standard", "max_attempts": 10}),
    )


def create_ami(
    config: MigrationConfig,
    record: InstanceRecord,
) -> InstanceRecord:
    ec2 = _ec2_client(config.region)
    ami_name = build_ami_name(config, record.instance_name)

    tags = [
        {"Key": "Name", "Value": ami_name},
        {"Key": "MigrationSourceInstanceId", "Value": record.instance_id},
        {"Key": "MigrationSourceInstanceName", "Value": record.instance_name},
        {"Key": "MigrationManagedBy", "Value": "ec2-ami-migration"},
        {"Key": "MigrationTargetAccount", "Value": config.target_account_id},
        {"Key": "MigrationCreatedAt", "Value": datetime.now(timezone.utc).isoformat()},
    ]

    def _create() -> str:
        response = ec2.create_image(
            InstanceId=record.instance_id,
            Name=ami_name,
            Description=(
                f"Migration AMI for {record.instance_name} ({record.instance_id})"
            ),
            NoReboot=config.no_reboot,
            TagSpecifications=[
                {
                    "ResourceType": "image",
                    "Tags": tags,
                },
                {"ResourceType": "snapshot", "Tags": tags},
            ],
        )
        return response["ImageId"]

    try:
        ami_id = with_retry(_create, operation=f"create_image({record.instance_id})")
        record.touch(
            status=InstanceStatus.CREATING,
            ami_id=ami_id,
            ami_name=ami_name,
            failure_reason=None,
        )
        logger.info(
            "Created AMI %s (%s) for instance %s",
            ami_id,
            ami_name,
            record.instance_id,
        )
    except Exception as exc:
        record.touch(status=InstanceStatus.FAILED, failure_reason=str(exc))
        logger.exception("Failed to create AMI for %s", record.instance_id)
    return record


def describe_amis(region: str, ami_ids: list[str]) -> dict[str, dict]:
    if not ami_ids:
        return {}
    ec2 = _ec2_client(region)

    def _describe() -> dict[str, dict]:
        response = ec2.describe_images(ImageIds=ami_ids)
        return {img["ImageId"]: img for img in response.get("Images", [])}

    try:
        return with_retry(_describe, operation="describe_images")
    except Exception as exc:
        logger.error("describe_images failed: %s", exc)
        return {}


def snapshot_ids_from_image(image: dict) -> list[str]:
    ids: list[str] = []
    for mapping in image.get("BlockDeviceMappings", []):
        ebs = mapping.get("Ebs")
        if ebs and ebs.get("SnapshotId"):
            ids.append(ebs["SnapshotId"])
    return ids


def share_ami_and_snapshots(
    config: MigrationConfig,
    record: InstanceRecord,
) -> InstanceRecord:
    if not record.ami_id:
        record.touch(
            status=InstanceStatus.FAILED,
            failure_reason="Missing AMI ID for sharing",
        )
        return record

    ec2 = _ec2_client(config.region)
    record.touch(status=InstanceStatus.SHARING)

    image_map = describe_amis(config.region, [record.ami_id])
    image = image_map.get(record.ami_id)
    if not image:
        record.touch(
            status=InstanceStatus.FAILED,
            failure_reason=f"AMI {record.ami_id} not found during sharing",
        )
        return record

    snapshot_ids = snapshot_ids_from_image(image)
    record.touch(snapshot_ids=snapshot_ids)

    def _share_ami() -> None:
        ec2.modify_image_attribute(
            ImageId=record.ami_id,
            LaunchPermission={"Add": [{"UserId": config.target_account_id}]},
        )

    def _share_snapshot(snapshot_id: str) -> None:
        ec2.modify_snapshot_attribute(
            Attribute="createVolumePermission",
            OperationType="add",
            SnapshotId=snapshot_id,
            UserIds=[config.target_account_id],
        )

    try:
        with_retry(_share_ami, operation=f"share_ami({record.ami_id})")
        for snap_id in snapshot_ids:
            with_retry(
                lambda sid=snap_id: _share_snapshot(sid),
                operation=f"share_snapshot({snap_id})",
            )
        record.touch(status=InstanceStatus.COMPLETED, failure_reason=None)
        logger.info(
            "Shared AMI %s and %s snapshot(s) with account %s",
            record.ami_id,
            len(snapshot_ids),
            config.target_account_id,
        )
    except Exception as exc:
        record.touch(status=InstanceStatus.FAILED, failure_reason=str(exc))
        logger.exception("Failed to share AMI %s", record.ami_id)
    return record


def check_ami_status(
    region: str,
    record: InstanceRecord,
) -> tuple[InstanceRecord, str | None]:
    """Return updated record and optional failure reason from image state."""
    if not record.ami_id:
        return record, "No AMI ID to monitor"

    images = describe_amis(region, [record.ami_id])
    image = images.get(record.ami_id)
    if not image:
        return record.touch(
            status=InstanceStatus.FAILED,
            failure_reason=f"AMI {record.ami_id} not found",
        ), f"AMI {record.ami_id} not found"

    state = image.get("State", "unknown")
    if state == "available":
        return record.touch(status=InstanceStatus.AVAILABLE), None
    if state == "failed":
        reason = image.get("StateReason", {}).get("Message", "AMI entered failed state")
        return record.touch(status=InstanceStatus.FAILED, failure_reason=reason), reason
    if state in ("pending", "transient"):
        return record, None
    return record, None
