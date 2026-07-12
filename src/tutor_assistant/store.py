from __future__ import annotations

import sqlite3
from pathlib import Path

from .domain import Lesson


class LessonStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self.connect() as db:
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

    def save(self, lesson: Lesson) -> None:
        payload = lesson.model_dump_json()
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

    def get(self, lesson_id: str) -> Lesson | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM lessons WHERE lesson_id=?", (lesson_id,)).fetchone()
        return Lesson.model_validate_json(row["payload"]) if row else None

    def list(self, limit: int = 100) -> list[Lesson]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM lessons ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Lesson.model_validate_json(row["payload"]) for row in rows]
