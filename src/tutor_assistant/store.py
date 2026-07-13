from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import TypeVar

from .domain import Lesson

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
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA synchronous=NORMAL")
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
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS lessons (
                    lesson_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    lesson_date TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS transcription_jobs (
                    lesson_id TEXT PRIMARY KEY,
                    audio_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(lesson_id) REFERENCES lessons(lesson_id)
                )
                """
            )

    def save(self, lesson: Lesson) -> None:
        payload = lesson.model_dump_json()

        def operation() -> None:
            with self.connect() as db:
                db.execute(
                    """
                    INSERT INTO lessons VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(lesson_id) DO UPDATE SET
                      student_id=excluded.student_id,
                      lesson_date=excluded.lesson_date,
                      topic=excluded.topic,
                      status=excluded.status,
                      payload=excluded.payload,
                      updated_at=excluded.updated_at
                    """,
                    (
                        lesson.lesson_id,
                        lesson.student.id,
                        lesson.lesson_date.isoformat(),
                        lesson.topic,
                        lesson.status.value,
                        payload,
                        lesson.updated_at.isoformat(),
                    ),
                )

        self._retry(operation)

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
                return db.execute(
                    "SELECT payload FROM lessons WHERE lesson_id=?", (lesson_id,)
                ).fetchone()

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
