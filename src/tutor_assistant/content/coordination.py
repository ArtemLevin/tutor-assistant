from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread, get_ident
from uuid import uuid4

from ..sqlite_utils import ClosingConnection


@dataclass(frozen=True, slots=True)
class ActivityLeaseInfo:
    lease_id: str
    owner_id: str
    activity: str
    lesson_id: str | None
    exclusive: bool
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime


class ContentBusyError(RuntimeError):
    """Raised when an incompatible operation is active in another process."""

    def __init__(
        self,
        message: str,
        *,
        blockers: tuple[ActivityLeaseInfo, ...] = (),
    ) -> None:
        super().__init__(message)
        self.blockers = blockers

    @classmethod
    def from_blockers(
        cls,
        blockers: tuple[ActivityLeaseInfo, ...],
    ) -> ContentBusyError:
        description = (
            ", ".join(
                f"{item.activity}{f' ({item.lesson_id})' if item.lesson_id else ''}"
                for item in blockers
            )
            or "неизвестная операция"
        )
        return cls(f"Хранилище занято: {description}", blockers=blockers)


@dataclass(frozen=True, slots=True)
class LeaseAcquireResult:
    lease_info: ActivityLeaseInfo | None
    blockers: tuple[ActivityLeaseInfo, ...] = ()

    @property
    def acquired(self) -> bool:
        return self.lease_info is not None


class ActivityLeaseStore:
    """Small, restore-independent SQLite store for cross-process leases."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_leases (
                    lease_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    activity TEXT NOT NULL,
                    lesson_id TEXT,
                    exclusive INTEGER NOT NULL CHECK(exclusive IN (0, 1)),
                    acquired_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS activity_leases_expiry ON activity_leases(expires_at)")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_state (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                )
                """
            )
            db.execute(
                "INSERT OR IGNORE INTO workspace_state(key, value) VALUES ('generation', 0)"
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA synchronous=FULL")
        except Exception:
            db.close()
            raise
        return db

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ActivityLeaseInfo:
        return ActivityLeaseInfo(
            lease_id=str(row["lease_id"]),
            owner_id=str(row["owner_id"]),
            activity=str(row["activity"]),
            lesson_id=row["lesson_id"],
            exclusive=bool(row["exclusive"]),
            acquired_at=datetime.fromisoformat(row["acquired_at"]),
            heartbeat_at=datetime.fromisoformat(row["heartbeat_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )

    def try_acquire(
        self,
        *,
        owner_id: str,
        activity: str,
        lesson_id: str | None = None,
        exclusive: bool = False,
        ttl: timedelta = timedelta(minutes=2),
    ) -> LeaseAcquireResult:
        if ttl.total_seconds() <= 0:
            raise ValueError("Lease TTL must be positive")
        now = self._now()
        expires_at = now + ttl
        lease_id = uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM activity_leases WHERE expires_at <= ?", (now.isoformat(),))
            if exclusive:
                rows = db.execute(
                    "SELECT * FROM activity_leases ORDER BY acquired_at, lease_id"
                ).fetchall()
            else:
                rows = db.execute(
                    """
                    SELECT * FROM activity_leases
                    WHERE exclusive=1
                       OR (? IS NOT NULL AND lesson_id=?)
                    ORDER BY acquired_at, lease_id
                    """,
                    (lesson_id, lesson_id),
                ).fetchall()
            blockers = tuple(self._from_row(row) for row in rows)
            if blockers:
                return LeaseAcquireResult(lease_info=None, blockers=blockers)
            db.execute(
                """
                INSERT INTO activity_leases (
                    lease_id, owner_id, activity, lesson_id, exclusive,
                    acquired_at, heartbeat_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lease_id,
                    owner_id,
                    activity,
                    lesson_id,
                    int(exclusive),
                    now.isoformat(),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        return LeaseAcquireResult(
            lease_info=ActivityLeaseInfo(
                lease_id=lease_id,
                owner_id=owner_id,
                activity=activity,
                lesson_id=lesson_id,
                exclusive=exclusive,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=expires_at,
            )
        )

    def acquire(
        self,
        *,
        owner_id: str,
        activity: str,
        lesson_id: str | None = None,
        exclusive: bool = False,
        ttl: timedelta = timedelta(minutes=2),
    ) -> ActivityLeaseInfo | None:
        return self.try_acquire(
            owner_id=owner_id,
            activity=activity,
            lesson_id=lesson_id,
            exclusive=exclusive,
            ttl=ttl,
        ).lease_info

    def heartbeat(self, lease_id: str, owner_id: str, ttl: timedelta) -> bool:
        now = self._now()
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE activity_leases
                SET heartbeat_at=?, expires_at=?
                WHERE lease_id=? AND owner_id=? AND expires_at>?
                """,
                (
                    now.isoformat(),
                    (now + ttl).isoformat(),
                    lease_id,
                    owner_id,
                    now.isoformat(),
                ),
            )
        return cursor.rowcount == 1

    def release(self, lease_id: str, owner_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM activity_leases WHERE lease_id=? AND owner_id=?",
                (lease_id, owner_id),
            )

    def active(self) -> list[ActivityLeaseInfo]:
        now = self._now()
        with self._connect() as db:
            db.execute("DELETE FROM activity_leases WHERE expires_at <= ?", (now.isoformat(),))
            rows = db.execute("SELECT * FROM activity_leases ORDER BY acquired_at, lease_id").fetchall()
        return [self._from_row(row) for row in rows]

    def generation(self) -> int:
        with self._connect() as db:
            row = db.execute(
                "SELECT value FROM workspace_state WHERE key='generation'"
            ).fetchone()
        return int(row["value"])

    def advance_generation(self) -> int:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE workspace_state SET value=value+1 WHERE key='generation'"
            )
            row = db.execute(
                "SELECT value FROM workspace_state WHERE key='generation'"
            ).fetchone()
        return int(row["value"])


class ActivityLease:
    def __init__(
        self,
        store: ActivityLeaseStore,
        info: ActivityLeaseInfo,
        ttl: timedelta,
        on_release: Callable[[ActivityLease], None] | None = None,
    ) -> None:
        self.store = store
        self.info = info
        self.ttl = ttl
        self._stop = Event()
        self._released = False
        self.origin_thread_id = get_ident()
        self._on_release = on_release
        interval = max(1.0, min(30.0, ttl.total_seconds() / 3))
        self._thread = Thread(
            target=self._heartbeat_loop,
            args=(interval,),
            name=f"content-lease-{info.activity}",
            daemon=True,
        )
        self._thread.start()

    def _heartbeat_loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            if not self.store.heartbeat(self.info.lease_id, self.info.owner_id, self.ttl):
                return

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._stop.set()
        try:
            self.store.release(self.info.lease_id, self.info.owner_id)
        finally:
            if self._on_release is not None:
                self._on_release(self)

    def __enter__(self) -> ActivityLease:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


def process_owner_id() -> str:
    return f"{os.getpid()}-{uuid4().hex}"
