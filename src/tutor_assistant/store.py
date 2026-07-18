from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import TypeVar

from .content.migrations import apply_migrations
from .content.repository import StudentContentRepository
from .domain import Lesson
from .sqlite_utils import ClosingConnection

T = TypeVar("T")


@dataclass(frozen=True)
class StoredTranscriptionJob:
    lesson_id: str
    audio_path: str
    status: str
    error: str | None
    attempts: int


class LessonStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _retry(operation: Callable[[], T]) -> T:
        for attempt in range(5):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 4:
                    raise
                sleep(0.05 * (2**attempt))
        raise RuntimeError("unreachable")

    def _initialize(self) -> None:
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            apply_migrations(db)

    def save(self, lesson: Lesson) -> None:
        """Create a legacy lesson; updates must use StudentContentService."""

        repository = StudentContentRepository(self.path)
        if repository.get_lesson(lesson.lesson_id, include_deleted=True) is not None:
            raise RuntimeError(
                "LessonStore.save() больше не обновляет занятия; используйте StudentContentService"
            )
        repository.insert_lesson(lesson)

    def save_transcription_job(
        self,
        lesson_id: str,
        audio_path: str,
        status: str,
        error: str | None = None,
        *,
        increment_attempts: bool = False,
    ) -> None:
        def operation() -> None:
            with self.connect() as db:
                db.execute(
                    """
                    INSERT INTO transcription_jobs
                        (lesson_id, audio_path, status, error, attempts, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(lesson_id) DO UPDATE SET
                        audio_path=excluded.audio_path,
                        status=excluded.status,
                        error=excluded.error,
                        attempts=transcription_jobs.attempts + ?,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        lesson_id,
                        audio_path,
                        status,
                        error,
                        1 if increment_attempts else 0,
                        1 if increment_attempts else 0,
                    ),
                )

        self._retry(operation)

    def list_transcription_jobs(self) -> list[StoredTranscriptionJob]:
        def operation():
            with self.connect() as db:
                return db.execute(
                    """
                    SELECT lesson_id, audio_path, status, error, attempts
                    FROM transcription_jobs
                    ORDER BY updated_at ASC
                    """
                ).fetchall()

        rows = self._retry(operation)
        return [StoredTranscriptionJob(**dict(row)) for row in rows]

    def get(self, lesson_id: str) -> Lesson | None:
        def operation():
            with self.connect() as db:
                return db.execute("SELECT payload FROM lessons WHERE lesson_id=?", (lesson_id,)).fetchone()

        row = self._retry(operation)
        return Lesson.model_validate_json(row["payload"]) if row else None

    def list(self, limit: int = 100) -> list[Lesson]:
        def operation():
            with self.connect() as db:
                return db.execute(
                    "SELECT payload FROM lessons ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()

        rows = self._retry(operation)
        return [Lesson.model_validate_json(row["payload"]) for row in rows]
