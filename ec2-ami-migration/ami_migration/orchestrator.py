"""Migration workflow orchestration."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ami_migration.ami_ops import check_ami_status, create_ami, share_ami_and_snapshots
from ami_migration.config import MigrationConfig
from ami_migration.discovery import DiscoveredInstance, discover_instances
from ami_migration.notifications import notify_ami_event, notify_summary
from ami_migration.state import InstanceRecord, InstanceStatus, StateStore

logger = logging.getLogger("ami_migration")


class MigrationOrchestrator:
    def __init__(self, config: MigrationConfig, state: StateStore) -> None:
        self.config = config
        self.state = state
        self._notified: set[str] = set()

    def _should_skip(self, record: InstanceRecord) -> bool:
        if record.status == InstanceStatus.COMPLETED:
            return True
        if record.status == InstanceStatus.FAILED:
            return not self.config.retry_failed_on_resume
        return False

    def _sync_discovered(self, discovered: list[DiscoveredInstance]) -> None:
        for inst in discovered:
            existing = self.state.get(inst.instance_id)
            if existing is None:
                record = InstanceRecord(
                    instance_id=inst.instance_id,
                    instance_name=inst.name,
                    instance_state=inst.state,
                    status=InstanceStatus.PENDING,
                )
                self.state.upsert(record)
            else:
                existing.touch(instance_name=inst.name, instance_state=inst.state)
                self.state.upsert(existing)

    def _log_progress(self, label: str) -> None:
        progress = self.state.progress_summary()
        logger.info(
            "%s | total=%s completed=%s failed=%s in_progress=%s",
            label,
            progress["total"],
            progress["completed"],
            progress["failed"],
            progress["in_progress"],
        )

    def _create_amis_parallel(self) -> None:
        pending: list[InstanceRecord] = []
        for record in self.state.all_records():
            if self._should_skip(record):
                continue
            if record.status in (
                InstanceStatus.PENDING,
                InstanceStatus.FAILED,
            ) and not record.ami_id:
                pending.append(record)
            elif record.status == InstanceStatus.FAILED and self.config.retry_failed_on_resume:
                record.touch(
                    status=InstanceStatus.PENDING,
                    ami_id=None,
                    ami_name=None,
                    failure_reason=None,
                    snapshot_ids=[],
                )
                self.state.upsert(record)
                pending.append(record)

        if not pending:
            logger.info("No new AMI creations required")
            return

        workers = min(self.config.max_parallel_workers, len(pending))
        logger.info("Creating %s AMI(s) with %s worker(s)", len(pending), workers)

        def _task(rec: InstanceRecord) -> InstanceRecord:
            return create_ami(self.config, rec)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="create") as pool:
            futures = {pool.submit(_task, rec): rec for rec in pending}
            for future in as_completed(futures):
                original = futures[future]
                try:
                    updated = future.result()
                except Exception as exc:
                    updated = original
                    updated.touch(
                        status=InstanceStatus.FAILED,
                        failure_reason=str(exc),
                    )
                    logger.exception("Unexpected error creating AMI for %s", original.instance_id)
                self.state.upsert(updated)
                self._log_progress("After AMI creation")

    def _process_available(self, record: InstanceRecord) -> InstanceRecord:
        updated = share_ami_and_snapshots(self.config, record)
        self.state.upsert(updated)
        if updated.instance_id not in self._notified:
            notify_ami_event(self.config, updated)
            self._notified.add(updated.instance_id)
        return updated

    def _handle_failed(self, record: InstanceRecord) -> None:
        self.state.upsert(record)
        if record.instance_id not in self._notified:
            notify_ami_event(self.config, record)
            self._notified.add(record.instance_id)

    def _monitor_loop(self) -> None:
        logger.info(
            "Starting AMI monitor loop (poll every %ss)",
            self.config.polling_interval_seconds,
        )
        while not self.state.all_terminal():
            in_flight = [
                r
                for r in self.state.all_records()
                if r.status
                in (
                    InstanceStatus.CREATING,
                    InstanceStatus.AVAILABLE,
                    InstanceStatus.SHARING,
                )
            ]

            if not in_flight:
                pending = [
                    r
                    for r in self.state.all_records()
                    if r.status == InstanceStatus.PENDING and not r.ami_id
                ]
                if pending:
                    logger.warning(
                        "%s instance(s) still pending without AMI; recreating",
                        len(pending),
                    )
                    self._create_amis_parallel()
                    continue
                break

            newly_available: list[InstanceRecord] = []
            for record in in_flight:
                if record.status == InstanceStatus.SHARING:
                    continue
                if record.status == InstanceStatus.AVAILABLE:
                    newly_available.append(record)
                    continue
                updated, failure = check_ami_status(self.config.region, record)
                if failure and updated.status == InstanceStatus.FAILED:
                    self._handle_failed(updated)
                elif updated.status == InstanceStatus.AVAILABLE:
                    self.state.upsert(updated)
                    newly_available.append(updated)
                else:
                    self.state.upsert(updated)

            if newly_available:
                workers = min(
                    self.config.max_parallel_workers,
                    len(newly_available),
                )
                with ThreadPoolExecutor(
                    max_workers=workers,
                    thread_name_prefix="share",
                ) as pool:
                    futures = {
                        pool.submit(self._process_available, rec): rec
                        for rec in newly_available
                    }
                    for future in as_completed(futures):
                        try:
                            future.result()
                        except Exception:
                            rec = futures[future]
                            logger.exception(
                                "Error sharing AMI for %s",
                                rec.instance_id,
                            )
                self._log_progress("After share/notify")

            if self.state.all_terminal():
                break
            time.sleep(self.config.polling_interval_seconds)

    def _restore_notified_from_state(self) -> None:
        for record in self.state.all_records():
            if record.status in (InstanceStatus.COMPLETED, InstanceStatus.FAILED):
                self._notified.add(record.instance_id)

    def run(self) -> int:
        discovered = discover_instances(self.config.region)
        if not discovered:
            logger.warning("No running or stopped instances found in %s", self.config.region)
            return 0

        self._sync_discovered(discovered)
        self._restore_notified_from_state()
        self._log_progress("Initial")

        self._create_amis_parallel()
        self._monitor_loop()

        progress = self.state.progress_summary()
        self._log_progress("Final")

        notify_summary(self.config, self.state.all_records(), progress)

        failed = progress.get("failed", 0)
        return 1 if failed else 0
