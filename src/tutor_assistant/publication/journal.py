from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4


class PublicationOperationStatus(StrEnum):
    PREPARED = "prepared"
    PUSHING = "pushing"
    REMOTE_VERIFIED = "remote_verified"
    COMPLETED = "completed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"
    CONFLICT = "conflict"


ACTIVE_STATUSES = {
    PublicationOperationStatus.PREPARED,
    PublicationOperationStatus.PUSHING,
    PublicationOperationStatus.REMOTE_VERIFIED,
    PublicationOperationStatus.INDETERMINATE,
}


@dataclass(frozen=True, slots=True)
class PublicationOperation:
    id: str
    lesson_id: str
    status: PublicationOperationStatus
    repository_full_name: str
    remote_name: str
    remote_url_sha256: str
    branch: str
    repository_path: str
    content_sha256: str
    content_size_bytes: int
    expected_remote_sha: str
    local_commit_sha: str | None
    remote_commit_sha: str | None
    error_code: str | None
    error_details: str | None
    created_at: datetime
    push_started_at: datetime | None
    remote_verified_at: datetime | None
    completed_at: datetime | None


class PublicationOperationConflict(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class PublicationOperationStore:
    """Crash-recoverable journal kept beside the local lesson archive."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS publication_operations (
                    id TEXT PRIMARY KEY,
                    lesson_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'prepared', 'pushing', 'remote_verified', 'completed',
                        'failed', 'indeterminate', 'conflict'
                    )),
                    repository_full_name TEXT NOT NULL,
                    remote_name TEXT NOT NULL,
                    remote_url_sha256 TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    repository_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    content_size_bytes INTEGER NOT NULL CHECK(content_size_bytes >= 0),
                    expected_remote_sha TEXT NOT NULL,
                    local_commit_sha TEXT,
                    remote_commit_sha TEXT,
                    error_code TEXT,
                    error_details TEXT,
                    created_at TEXT NOT NULL,
                    push_started_at TEXT,
                    remote_verified_at TEXT,
                    completed_at TEXT
                )
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS publication_operations_lesson_created
                ON publication_operations(lesson_id, created_at DESC)
                """
            )
            db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS publication_operations_active_lesson
                ON publication_operations(lesson_id)
                WHERE status IN ('prepared', 'pushing', 'remote_verified', 'indeterminate')
                """
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PublicationOperation:
        return PublicationOperation(
            id=str(row["id"]),
            lesson_id=str(row["lesson_id"]),
            status=PublicationOperationStatus(str(row["status"])),
            repository_full_name=str(row["repository_full_name"]),
            remote_name=str(row["remote_name"]),
            remote_url_sha256=str(row["remote_url_sha256"]),
            branch=str(row["branch"]),
            repository_path=str(row["repository_path"]),
            content_sha256=str(row["content_sha256"]),
            content_size_bytes=int(row["content_size_bytes"]),
            expected_remote_sha=str(row["expected_remote_sha"]),
            local_commit_sha=row["local_commit_sha"],
            remote_commit_sha=row["remote_commit_sha"],
            error_code=row["error_code"],
            error_details=row["error_details"],
            created_at=datetime.fromisoformat(str(row["created_at"])),
            push_started_at=_datetime(row["push_started_at"]),
            remote_verified_at=_datetime(row["remote_verified_at"]),
            completed_at=_datetime(row["completed_at"]),
        )

    def get(self, operation_id: str) -> PublicationOperation:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM publication_operations WHERE id=?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"Publication operation not found: {operation_id}")
        return self._from_row(row)

    def active_for_lesson(self, lesson_id: str) -> PublicationOperation | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM publication_operations
                WHERE lesson_id=?
                  AND status IN ('prepared', 'pushing', 'remote_verified', 'indeterminate')
                ORDER BY created_at DESC LIMIT 1
                """,
                (lesson_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def begin(
        self,
        *,
        lesson_id: str,
        repository_full_name: str,
        remote_name: str,
        remote_url_sha256: str,
        branch: str,
        repository_path: str,
        content_sha256: str,
        content_size_bytes: int,
        expected_remote_sha: str,
    ) -> PublicationOperation:
        operation_id = uuid4().hex
        try:
            with self._connect() as db:
                db.execute(
                    """
                    INSERT INTO publication_operations (
                        id, lesson_id, status, repository_full_name, remote_name,
                        remote_url_sha256, branch, repository_path, content_sha256,
                        content_size_bytes, expected_remote_sha, created_at
                    ) VALUES (?, ?, 'prepared', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation_id,
                        lesson_id,
                        repository_full_name,
                        remote_name,
                        remote_url_sha256,
                        branch,
                        repository_path,
                        content_sha256,
                        content_size_bytes,
                        expected_remote_sha,
                        _now(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if (
                getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_CONSTRAINT_UNIQUE
                or self.active_for_lesson(lesson_id) is None
            ):
                raise
            raise PublicationOperationConflict(f"Для занятия {lesson_id} уже выполняется публикация") from exc
        return self.get(operation_id)

    def _update(
        self,
        operation_id: str,
        *,
        expected: tuple[PublicationOperationStatus, ...],
        status: PublicationOperationStatus,
        assignments: dict[str, object | None] | None = None,
    ) -> PublicationOperation:
        values = dict(assignments or {})
        values["status"] = status.value
        columns = ", ".join(f"{column}=?" for column in values)
        parameters = [*values.values(), operation_id, *(item.value for item in expected)]
        placeholders = ", ".join("?" for _ in expected)
        with self._connect() as db:
            cursor = db.execute(
                f"UPDATE publication_operations SET {columns} WHERE id=? AND status IN ({placeholders})",
                parameters,
            )
        if cursor.rowcount != 1:
            current = self.get(operation_id)
            raise PublicationOperationConflict(
                f"Недопустимый переход publication operation: {current.status.value} → {status.value}"
            )
        return self.get(operation_id)

    def mark_pushing(self, operation_id: str, local_commit_sha: str) -> PublicationOperation:
        return self._update(
            operation_id,
            expected=(PublicationOperationStatus.PREPARED,),
            status=PublicationOperationStatus.PUSHING,
            assignments={
                "local_commit_sha": local_commit_sha,
                "push_started_at": _now(),
                "error_code": None,
                "error_details": None,
            },
        )

    def mark_remote_verified(
        self,
        operation_id: str,
        remote_commit_sha: str,
        *,
        allow_prepared: bool = False,
    ) -> PublicationOperation:
        expected = (
            (
                PublicationOperationStatus.PREPARED,
                PublicationOperationStatus.PUSHING,
                PublicationOperationStatus.INDETERMINATE,
            )
            if allow_prepared
            else (
                PublicationOperationStatus.PUSHING,
                PublicationOperationStatus.INDETERMINATE,
            )
        )
        return self._update(
            operation_id,
            expected=expected,
            status=PublicationOperationStatus.REMOTE_VERIFIED,
            assignments={
                "remote_commit_sha": remote_commit_sha,
                "remote_verified_at": _now(),
                "error_code": None,
                "error_details": None,
            },
        )

    def mark_completed(self, operation_id: str) -> PublicationOperation:
        return self._update(
            operation_id,
            expected=(PublicationOperationStatus.REMOTE_VERIFIED,),
            status=PublicationOperationStatus.COMPLETED,
            assignments={"completed_at": _now()},
        )

    def mark_failed(
        self,
        operation_id: str,
        *,
        error_code: str,
        details: str,
    ) -> PublicationOperation:
        return self._update(
            operation_id,
            expected=tuple(ACTIVE_STATUSES),
            status=PublicationOperationStatus.FAILED,
            assignments={
                "error_code": error_code,
                "error_details": details[-3000:],
                "completed_at": _now(),
            },
        )

    def mark_indeterminate(
        self,
        operation_id: str,
        *,
        error_code: str,
        details: str,
    ) -> PublicationOperation:
        return self._update(
            operation_id,
            expected=(PublicationOperationStatus.PUSHING,),
            status=PublicationOperationStatus.INDETERMINATE,
            assignments={
                "error_code": error_code,
                "error_details": details[-3000:],
            },
        )

    def mark_conflict(
        self,
        operation_id: str,
        *,
        remote_commit_sha: str | None,
        details: str,
    ) -> PublicationOperation:
        return self._update(
            operation_id,
            expected=tuple(ACTIVE_STATUSES),
            status=PublicationOperationStatus.CONFLICT,
            assignments={
                "remote_commit_sha": remote_commit_sha,
                "error_code": "remote_advanced",
                "error_details": details[-3000:],
                "completed_at": _now(),
            },
        )
