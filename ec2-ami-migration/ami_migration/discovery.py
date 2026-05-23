"""EC2 instance discovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import boto3
from botocore.config import Config

from ami_migration.retry import with_retry

logger = logging.getLogger("ami_migration")

PROCESSABLE_STATES = frozenset({"running", "stopped"})


@dataclass(frozen=True)
class DiscoveredInstance:
    instance_id: str
    name: str
    state: str


def _instance_name(tags: list[dict[str, str]] | None) -> str:
    if not tags:
        return ""
    for tag in tags:
        if tag.get("Key") == "Name" and tag.get("Value"):
            return tag["Value"]
    return ""


def discover_instances(region: str) -> list[DiscoveredInstance]:
    client = boto3.client(
        "ec2",
        region_name=region,
        config=Config(retries={"mode": "standard", "max_attempts": 10}),
    )
    instances: list[DiscoveredInstance] = []
    paginator = client.get_paginator("describe_instances")

    def _fetch() -> list[DiscoveredInstance]:
        found: list[DiscoveredInstance] = []
        for page in paginator.paginate(
            Filters=[{"Name": "instance-state-name", "Values": sorted(PROCESSABLE_STATES)}]
        ):
            for reservation in page.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    state = inst.get("State", {}).get("Name", "unknown")
                    if state not in PROCESSABLE_STATES:
                        continue
                    iid = inst["InstanceId"]
                    name = _instance_name(inst.get("Tags")) or iid
                    found.append(
                        DiscoveredInstance(instance_id=iid, name=name, state=state)
                    )
        return found

    instances = with_retry(_fetch, operation="describe_instances")
    logger.info(
        "Discovered %s instance(s) in %s (%s)",
        len(instances),
        region,
        ", ".join(sorted(PROCESSABLE_STATES)),
    )
    return instances
