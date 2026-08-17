from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from .crm import CrmStore, ScheduledLesson


class ScheduledLessonStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ScheduleStatusSummary:
    active_lessons: int
    cancelled_lessons: int
    total_lessons: int
    planned_revenue_cents: int


def summarize_schedule(lessons: Iterable[ScheduledLesson]) -> ScheduleStatusSummary:
    items = list(lessons)
    active = [item for item in items if item.status != ScheduledLessonStatus.CANCELLED.value]
    return ScheduleStatusSummary(
        active_lessons=len(active),
        cancelled_lessons=len(items) - len(active),
        total_lessons=len(items),
        planned_revenue_cents=sum(item.rate_cents for item in active),
    )


def set_scheduled_lesson_status(
    store: CrmStore,
    lesson: ScheduledLesson,
    status: ScheduledLessonStatus | str,
) -> int:
    """Materialize the selected occurrence and change its status atomically.

    For a recurring rule this creates/updates only the selected date exception. The
    underlying weekly rule remains active, so cancelling one lesson never silently
    removes future lessons in the series.
    """

    target = ScheduledLessonStatus(status)

    def operation() -> int:
        now = store._now()
        with store.connect() as db:
            occurrence_id = lesson.occurrence_id
            if occurrence_id is None and lesson.rule_id is None:
                cursor = db.execute(
                    """
                    INSERT INTO crm_lesson_occurrences (
                        rule_id, original_date, student_id, starts_at, duration_minutes,
                        subject, topic, meeting_secret, status, rate_cents, paid,
                        lesson_id, created_at, updated_at
                    ) VALUES (NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lesson.student_id,
                        lesson.starts_at.isoformat(),
                        lesson.duration_minutes,
                        lesson.subject,
                        lesson.topic,
                        store.codec.encrypt(lesson.meeting_url),
                        target.value,
                        lesson.rate_cents,
                        int(lesson.paid),
                        lesson.lesson_id,
                        now,
                        now,
                    ),
                )
                return int(cursor.lastrowid)

            if occurrence_id is None:
                original_date = lesson.original_date or lesson.starts_at.date()
                db.execute(
                    """
                    INSERT INTO crm_lesson_occurrences (
                        rule_id, original_date, student_id, starts_at, duration_minutes,
                        subject, topic, meeting_secret, status, rate_cents, paid,
                        lesson_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(rule_id, original_date) DO UPDATE SET
                        status=excluded.status,
                        updated_at=excluded.updated_at
                    """,
                    (
                        lesson.rule_id,
                        original_date.isoformat(),
                        lesson.student_id,
                        lesson.starts_at.isoformat(),
                        lesson.duration_minutes,
                        lesson.subject,
                        lesson.topic,
                        store.codec.encrypt(lesson.meeting_url),
                        target.value,
                        lesson.rate_cents,
                        int(lesson.paid),
                        lesson.lesson_id,
                        now,
                        now,
                    ),
                )
                row = db.execute(
                    "SELECT id FROM crm_lesson_occurrences WHERE rule_id=? AND original_date=?",
                    (lesson.rule_id, original_date.isoformat()),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Не удалось материализовать занятие расписания")
                return int(row["id"])

            db.execute(
                """
                UPDATE crm_lesson_occurrences
                SET status=?, updated_at=?
                WHERE id=?
                """,
                (target.value, now, occurrence_id),
            )
            return occurrence_id

    return store._retry(operation)
