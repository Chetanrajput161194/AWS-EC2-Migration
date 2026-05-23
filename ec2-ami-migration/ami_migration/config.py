"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class MigrationConfig:
    target_account_id: str
    sns_topic_arn: str
    region: str
    max_parallel_workers: int
    polling_interval_seconds: int
    ami_name_prefix: str
    state_file: Path
    log_file: Path
    no_reboot: bool
    retry_failed_on_resume: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MigrationConfig:
        required = ("target_account_id", "sns_topic_arn", "region")
        missing = [k for k in required if not data.get(k)]
        if missing:
            raise ValueError(f"Missing required config keys: {', '.join(missing)}")

        workers = int(data.get("max_parallel_workers", 5))
        if workers < 1:
            raise ValueError("max_parallel_workers must be >= 1")

        interval = int(data.get("polling_interval_seconds", 30))
        if interval < 5:
            raise ValueError("polling_interval_seconds must be >= 5")

        return cls(
            target_account_id=str(data["target_account_id"]).strip(),
            sns_topic_arn=str(data["sns_topic_arn"]).strip(),
            region=str(data["region"]).strip(),
            max_parallel_workers=workers,
            polling_interval_seconds=interval,
            ami_name_prefix=str(data.get("ami_name_prefix", "") or ""),
            state_file=Path(data.get("state_file", "migration_state.json")),
            log_file=Path(data.get("log_file", "ami_migration.log")),
            no_reboot=bool(data.get("no_reboot", False)),
            retry_failed_on_resume=bool(data.get("retry_failed_on_resume", False)),
        )


def load_config(path: Path) -> MigrationConfig:
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a mapping")
    return MigrationConfig.from_dict(raw)
