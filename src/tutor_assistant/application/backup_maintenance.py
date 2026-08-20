"""Qt-free scheduling, verification and reason-aware backup retention."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Protocol

from ..atomic_io import atomic_write_text
from ..content.models import (
    DatabaseBackupInfo,
    DatabaseBackupRetentionResult,
    DatabaseBackupVerification,
)
from ..security.redaction import redact_text

LOGGER = logging.getLogger(__name__)
SCHEDULED_BACKUP_REASONS = frozenset({"scheduled", "scheduled-maintenance"})
BLOCKING_ACTIVITY_PREFIXES = (
    "recording",
    "database-restore",
    "database-backup",
    "backup-retention",
    "content-repair",
    "content-maintenance-apply",
    "startup-recovery",
    "shutdown",
)


class BackupServicePort(Protocol):
    def list_database_backups(self) -> list[DatabaseBackupInfo]: ...

    def create_database_backup(self, *, reason: str = "manual") -> DatabaseBackupInfo: ...

    def verify_database_backup(self, path: Path) -> DatabaseBackupVerification: ...

    def prune_database_backups(
        self, keep: int, *, reasons: frozenset[str] | None = None
    ) -> DatabaseBackupRetentionResult: ...

    def active_activities(self) -> Sequence[object]: ...


class BackupAction(StrEnum):
    RUN = "run"
    DISABLED = "disabled"
    NOT_DUE = "not-due"
    BLOCKED = "blocked"
    RUNNING = "running"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class BackupDecision:
    action: BackupAction
    reason: str
    next_due_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BackupMaintenanceSnapshot:
    enabled: bool
    interval_hours: int
    retention_count: int
    last_successful_at: datetime | None
    last_attempt_at: datetime | None
    next_due_at: datetime | None
    scheduled_copy_count: int
    running: bool
    verified: bool
    last_error: str | None
    last_backup_id: str | None
    last_pruned_count: int

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("last_successful_at", "last_attempt_at", "next_due_at"):
            value = payload[key]
            payload[key] = value.isoformat() if isinstance(value, datetime) else None
        return payload


class BackupMaintenanceCoordinator:
    def __init__(
        self,
        service: BackupServicePort,
        workspace: Path,
        *,
        enabled: bool,
        interval_hours: int,
        retention_count: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if interval_hours < 1 or retention_count < 1:
            raise ValueError("Backup interval and retention must both be positive")
        self._service = service
        self._workspace = workspace.expanduser().resolve()
        self._enabled = enabled
        self._interval = timedelta(hours=interval_hours)
        self._retention_count = retention_count
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._running = False
        self._shutdown = False
        self._last_successful_at: datetime | None = None
        self._last_attempt_at: datetime | None = None
        self._last_error: str | None = None
        self._last_backup_id: str | None = None
        self._last_pruned_count = 0
        self._verified = False
        self._restore_state()

    @property
    def status_path(self) -> Path:
        return self._workspace / "maintenance" / "backup-status.json"

    def _scheduled_backups(self) -> list[DatabaseBackupInfo]:
        return [
            backup
            for backup in self._service.list_database_backups()
            if backup.manifest.reason in SCHEDULED_BACKUP_REASONS
        ]

    def _restore_state(self) -> None:
        try:
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("backup status must contain an object")
            value = payload.get("last_successful_at")
            self._last_successful_at = datetime.fromisoformat(value) if isinstance(value, str) else None
            value = payload.get("last_attempt_at")
            self._last_attempt_at = datetime.fromisoformat(value) if isinstance(value, str) else None
            self._last_error = payload.get("last_error")
            self._last_backup_id = payload.get("last_backup_id")
            self._verified = bool(payload.get("verified"))
            self._last_pruned_count = int(payload.get("last_pruned_count", 0))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            LOGGER.warning("Backup maintenance status is malformed; rebuilding from manifests")
            self._last_successful_at = None

        scheduled = self._scheduled_backups()
        if self._last_successful_at is not None:
            matching = next(
                (item for item in scheduled if item.manifest.backup_id == self._last_backup_id),
                None,
            )
            if matching is None or not self._service.verify_database_backup(matching.path).valid:
                self._last_successful_at = None
                self._verified = False
        if self._last_successful_at is None:
            for backup in scheduled:
                verification = self._service.verify_database_backup(backup.path)
                if verification.valid:
                    self._last_successful_at = backup.manifest.created_at
                    self._last_backup_id = backup.manifest.backup_id
                    self._verified = True
                    break

    def snapshot(self) -> BackupMaintenanceSnapshot:
        next_due = (
            self._last_successful_at + self._interval
            if self._enabled and self._last_successful_at is not None
            else None
        )
        return BackupMaintenanceSnapshot(
            enabled=self._enabled,
            interval_hours=int(self._interval.total_seconds() // 3600),
            retention_count=self._retention_count,
            last_successful_at=self._last_successful_at,
            last_attempt_at=self._last_attempt_at,
            next_due_at=next_due,
            scheduled_copy_count=len(self._scheduled_backups()),
            running=self._running,
            verified=self._verified,
            last_error=self._last_error,
            last_backup_id=self._last_backup_id,
            last_pruned_count=self._last_pruned_count,
        )

    def _persist(self) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.status_path,
            json.dumps(self.snapshot().to_dict(), ensure_ascii=False, indent=2),
        )

    def decide(
        self,
        *,
        now: datetime | None = None,
        recording_active: bool = False,
        shutdown_requested: bool = False,
    ) -> BackupDecision:
        current = now or self._clock()
        if not self._enabled:
            return BackupDecision(BackupAction.DISABLED, "Automatic backups are disabled")
        if shutdown_requested or self._shutdown:
            return BackupDecision(BackupAction.SHUTDOWN, "Application shutdown is draining")
        if self._running:
            return BackupDecision(BackupAction.RUNNING, "A backup cycle is already running")
        if recording_active:
            return BackupDecision(BackupAction.BLOCKED, "Recording is active")
        blockers = [
            str(getattr(item, "activity", item))
            for item in self._service.active_activities()
            if str(getattr(item, "activity", item)).startswith(BLOCKING_ACTIVITY_PREFIXES)
        ]
        if blockers:
            return BackupDecision(BackupAction.BLOCKED, ", ".join(sorted(set(blockers))))
        next_due = (
            self._last_successful_at + self._interval if self._last_successful_at is not None else current
        )
        if current < next_due:
            return BackupDecision(BackupAction.NOT_DUE, "Backup interval has not elapsed", next_due)
        return BackupDecision(BackupAction.RUN, "A verified scheduled backup is due", next_due)

    def request_shutdown(self) -> None:
        self._shutdown = True

    def run_due(
        self,
        *,
        now: datetime | None = None,
        recording_active: bool = False,
        shutdown_requested: bool = False,
    ) -> BackupMaintenanceSnapshot:
        if not self._lock.acquire(blocking=False):
            return self.snapshot()
        try:
            decision = self.decide(
                now=now,
                recording_active=recording_active,
                shutdown_requested=shutdown_requested,
            )
            if decision.action is not BackupAction.RUN:
                self._persist()
                return self.snapshot()
            self._running = True
            self._last_attempt_at = now or self._clock()
            self._persist()
            try:
                backup = self._service.create_database_backup(reason="scheduled")
                verification = self._service.verify_database_backup(backup.path)
                if not verification.valid:
                    raise RuntimeError("; ".join(verification.errors) or "Backup verification failed")
                retention = self._service.prune_database_backups(
                    self._retention_count,
                    reasons=SCHEDULED_BACKUP_REASONS,
                )
                self._last_successful_at = backup.manifest.created_at
                self._last_backup_id = backup.manifest.backup_id
                self._verified = True
                self._last_pruned_count = len(retention.removed)
                self._last_error = redact_text("; ".join(retention.errors)) or None
                LOGGER.info(
                    "Scheduled backup verified: id=%s retained=%s pruned=%s",
                    backup.manifest.backup_id,
                    self._retention_count,
                    self._last_pruned_count,
                )
            except Exception as exc:
                self._last_error = redact_text(f"{type(exc).__name__}: {exc}")
                LOGGER.exception("Scheduled backup failed; existing backups were not pruned")
            finally:
                self._running = False
                self._persist()
            return self.snapshot()
        finally:
            self._lock.release()
