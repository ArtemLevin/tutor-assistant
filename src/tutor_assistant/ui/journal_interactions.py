from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from ..crm import ScheduledLesson
from ..lesson_journal import LessonJournalService


@dataclass(frozen=True, slots=True)
class HomeworkSnapshot:
    occurrence_id: int | None
    existed: bool
    assigned_at: datetime | None = None
    sent_at: datetime | None = None
    due_at: datetime | None = None
    received_at: datetime | None = None
    checked_at: datetime | None = None
    returned_at: datetime | None = None


@dataclass(slots=True)
class JournalUndoAction:
    label: str
    undo: Callable[[], None]


class ReversibleLessonJournalService(LessonJournalService):
    """Lesson journal service with exact homework snapshots for one-step Undo."""

    @staticmethod
    def _parse(value: object) -> datetime | None:
        if value is None:
            return None
        text = str(value).strip()
        return datetime.fromisoformat(text) if text else None

    def _existing_occurrence_id(self, lesson: ScheduledLesson) -> int | None:
        if lesson.occurrence_id is not None:
            return lesson.occurrence_id
        with self.crm_store.connect() as db:
            if lesson.rule_id is not None:
                original_date = lesson.original_date or lesson.starts_at.date()
                row = db.execute(
                    """
                    SELECT id
                    FROM crm_lesson_occurrences
                    WHERE rule_id=? AND original_date=?
                    """,
                    (lesson.rule_id, original_date.isoformat()),
                ).fetchone()
            else:
                row = db.execute(
                    """
                    SELECT id
                    FROM crm_lesson_occurrences
                    WHERE student_id=? AND starts_at=?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (lesson.student_id, lesson.starts_at.isoformat()),
                ).fetchone()
        return int(row["id"]) if row else None

    def snapshot_homework(self, lesson: ScheduledLesson) -> HomeworkSnapshot:
        occurrence_id = self._existing_occurrence_id(lesson)
        if occurrence_id is None:
            return HomeworkSnapshot(occurrence_id=None, existed=False)
        with self.crm_store.connect() as db:
            row = db.execute(
                """
                SELECT assigned_at, sent_at, due_at, received_at, checked_at, returned_at
                FROM crm_lesson_homework
                WHERE occurrence_id=?
                """,
                (occurrence_id,),
            ).fetchone()
        if row is None:
            return HomeworkSnapshot(occurrence_id=occurrence_id, existed=False)
        return HomeworkSnapshot(
            occurrence_id=occurrence_id,
            existed=True,
            assigned_at=self._parse(row["assigned_at"]),
            sent_at=self._parse(row["sent_at"]),
            due_at=self._parse(row["due_at"]),
            received_at=self._parse(row["received_at"]),
            checked_at=self._parse(row["checked_at"]),
            returned_at=self._parse(row["returned_at"]),
        )

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat(timespec="seconds") if value is not None else None

    def restore_homework(
        self,
        lesson: ScheduledLesson,
        snapshot: HomeworkSnapshot,
    ) -> int:
        occurrence_id = snapshot.occurrence_id
        if occurrence_id is None:
            occurrence_id = self.crm_store.ensure_occurrence(lesson)
        with self.crm_store.connect() as db:
            if not snapshot.existed:
                db.execute(
                    "DELETE FROM crm_lesson_homework WHERE occurrence_id=?",
                    (occurrence_id,),
                )
                return occurrence_id
            db.execute(
                """
                INSERT INTO crm_lesson_homework (
                    occurrence_id, assigned_at, sent_at, due_at,
                    received_at, checked_at, returned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(occurrence_id) DO UPDATE SET
                    assigned_at=excluded.assigned_at,
                    sent_at=excluded.sent_at,
                    due_at=excluded.due_at,
                    received_at=excluded.received_at,
                    checked_at=excluded.checked_at,
                    returned_at=excluded.returned_at
                """,
                (
                    occurrence_id,
                    self._iso(snapshot.assigned_at),
                    self._iso(snapshot.sent_at),
                    self._iso(snapshot.due_at),
                    self._iso(snapshot.received_at),
                    self._iso(snapshot.checked_at),
                    self._iso(snapshot.returned_at),
                ),
            )
        return occurrence_id


def logical_identity(lesson: ScheduledLesson) -> tuple[str, datetime]:
    return lesson.student_id, lesson.starts_at


def period_label(day: date) -> str:
    return day.strftime("%d.%m.%Y")
