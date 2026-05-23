"""Persistent migration state for resume support."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("ami_migration")


class InstanceStatus(str, Enum):
    PENDING = "pending"
    CREATING = "creating"
    AVAILABLE = "available"
    SHARING = "sharing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


TERMINAL_STATUSES = frozenset(
    {InstanceStatus.COMPLETED, InstanceStatus.FAILED, InstanceStatus.SKIPPED}
)


@dataclass
class InstanceRecord:
    instance_id: str
    instance_name: str
    instance_state: str
    status: InstanceStatus = InstanceStatus.PENDING
    ami_id: str | None = None
    ami_name: str | None = None
    snapshot_ids: list[str] = field(default_factory=list)
    failure_reason: str | None = None
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def touch(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstanceRecord:
        status = InstanceStatus(data.get("status", InstanceStatus.PENDING.value))
        return cls(
            instance_id=data["instance_id"],
            instance_name=data.get("instance_name", data["instance_id"]),
            instance_state=data.get("instance_state", "unknown"),
            status=status,
            ami_id=data.get("ami_id"),
            ami_name=data.get("ami_name"),
            snapshot_ids=list(data.get("snapshot_ids") or []),
            failure_reason=data.get("failure_reason"),
            updated_at=data.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._records: dict[str, InstanceRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            instances = raw.get("instances", {})
            for instance_id, payload in instances.items():
                self._records[instance_id] = InstanceRecord.from_dict(payload)
            logger.info("Loaded state for %s instance(s) from %s", len(self._records), self.path)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error("Could not load state file %s: %s", self.path, exc)
            raise

    def save(self) -> None:
        with self._lock:
            payload = {
                "version": 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "instances": {
                    iid: rec.to_dict() for iid, rec in self._records.items()
                },
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def get(self, instance_id: str) -> InstanceRecord | None:
        return self._records.get(instance_id)

    def upsert(self, record: InstanceRecord) -> None:
        with self._lock:
            self._records[record.instance_id] = record
        self.save()

    def all_records(self) -> list[InstanceRecord]:
        return list(self._records.values())

    def progress_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {s.value: 0 for s in InstanceStatus}
        for rec in self._records.values():
            counts[rec.status.value] = counts.get(rec.status.value, 0) + 1
        total = len(self._records)
        completed = counts.get(InstanceStatus.COMPLETED.value, 0)
        failed = counts.get(InstanceStatus.FAILED.value, 0)
        skipped = counts.get(InstanceStatus.SKIPPED.value, 0)
        terminal = completed + failed + skipped
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "in_progress": total - terminal,
            "terminal": terminal,
            **counts,
        }

    def all_terminal(self) -> bool:
        if not self._records:
            return True
        return all(r.status in TERMINAL_STATUSES for r in self._records.values())
