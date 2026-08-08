from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tutor_assistant.crm import CrmStore, ScheduleRule, ScheduledLesson
from tutor_assistant.domain import Student
from tutor_assistant.lesson_journal import (
    HomeworkStatus,
    LessonJournalFilter,
    LessonJournalService,
)


class TestCodec:
    def encrypt(self, value: str | None) -> str | None:
        return value

    def decrypt(self, value: str | None) -> str | None:
        return value


@pytest.fixture
def store(tmp_path: Path) -> CrmStore:
    value = CrmStore(tmp_path / "journal.sqlite3", TestCodec())
    value.sync_students(
        [
            Student(id="xenia", full_name="Ксения", subjects=["chemistry"]),
            Student(id="anna", full_name="Анна", subjects=["mathematics"]),
        ]
    )
    return value


def _count_occurrences(store: CrmStore) -> int:
    with sqlite3.connect(store.path) as db:
        return int(db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0])


def test_journal_projects_recurring_lessons_without_materializing(store: CrmStore) -> None:
    store.save_schedule_rule(
        ScheduleRule(
            student_id="xenia",
            weekday=1,
            start_minute=16 * 60,
            duration_minutes=90,
            subject="chemistry",
            topic="Алканы",
            valid_from=date(2026, 8, 1),
            rate_cents=300_000,
        )
    )
    service = LessonJournalService(store)

    result = service.search(
        LessonJournalFilter(
            date_from=date(2026, 8, 3),
            date_to=date(2026, 8, 16),
        ),
        now=datetime(2026, 8, 8, 20, 0),
    )

    assert [row.lesson.starts_at.date() for row in result.rows] == [
        date(2026, 8, 11),
        date(2026, 8, 4),
    ]
    assert all(row.lesson.occurrence_id is None for row in result.rows)
    assert _count_occurrences(store) == 0


def test_payment_and_homework_are_isolated_per_recurring_date(store: CrmStore) -> None:
    store.save_schedule_rule(
        ScheduleRule(
            student_id="xenia",
            weekday=1,
            start_minute=16 * 60,
            subject="chemistry",
            valid_from=date(2026, 8, 1),
            rate_cents=300_000,
        )
    )
    service = LessonJournalService(store)
    first = service.search(
        LessonJournalFilter(date_from=date(2026, 8, 3), date_to=date(2026, 8, 9))
    ).rows[0]

    service.set_paid(first.lesson, True)
    service.set_homework_status(
        first.lesson,
        HomeworkStatus.RECEIVED,
        at=datetime(2026, 8, 5, 18, 0),
    )

    updated = service.search(
        LessonJournalFilter(date_from=date(2026, 8, 3), date_to=date(2026, 8, 16)),
        now=datetime(2026, 8, 12, 12, 0),
    )
    first_row = next(
        row for row in updated.rows if row.lesson.starts_at.date() == date(2026, 8, 4)
    )
    next_row = next(
        row for row in updated.rows if row.lesson.starts_at.date() == date(2026, 8, 11)
    )
    assert first_row.lesson.paid is True
    assert first_row.homework_status == HomeworkStatus.RECEIVED
    assert first_row.requires_attention is True
    assert next_row.lesson.paid is False
    assert next_row.homework_status == HomeworkStatus.NONE
    assert next_row.lesson.occurrence_id is None
    assert _count_occurrences(store) == 1


def test_homework_state_can_move_backward_and_due_filter_works(store: CrmStore) -> None:
    service = LessonJournalService(store)
    lesson = ScheduledLesson(
        student_id="anna",
        student_name="Анна",
        starts_at=datetime(2026, 8, 3, 17, 0),
        duration_minutes=60,
        subject="mathematics",
        topic="Квадратные уравнения",
        rate_cents=250_000,
    )
    store.save_one_off(lesson)
    stored = service.search(
        LessonJournalFilter(date_from=date(2026, 8, 3), date_to=date(2026, 8, 3))
    ).rows[0]

    service.set_homework_status(
        stored.lesson,
        HomeworkStatus.RETURNED,
        at=datetime(2026, 8, 3, 19),
    )
    service.set_homework_status(
        stored.lesson,
        HomeworkStatus.SENT,
        at=datetime(2026, 8, 3, 20),
    )
    service.set_homework_due(stored.lesson, datetime(2026, 8, 4, 12))

    refreshed = service.search(
        LessonJournalFilter(date_from=date(2026, 8, 3), date_to=date(2026, 8, 3)),
        now=datetime(2026, 8, 5, 12),
    ).rows[0]
    assert refreshed.homework_status == HomeworkStatus.SENT
    assert refreshed.homework is not None
    assert refreshed.homework.received_at is None
    assert refreshed.homework.checked_at is None
    assert refreshed.homework.returned_at is None
    overdue = service.search(
        LessonJournalFilter(
            date_from=date(2026, 8, 3),
            date_to=date(2026, 8, 3),
            homework="overdue",
        ),
        now=datetime(2026, 8, 5, 12),
    )
    assert overdue.total == 1


def test_filters_summary_search_and_pagination(store: CrmStore) -> None:
    service = LessonJournalService(store)
    for day, student_id, name, subject, paid in (
        (3, "anna", "Анна", "mathematics", True),
        (4, "xenia", "Ксения", "chemistry", False),
        (5, "anna", "Анна", "mathematics", False),
    ):
        store.save_one_off(
            ScheduledLesson(
                student_id=student_id,
                student_name=name,
                starts_at=datetime(2026, 8, day, 18, 0),
                duration_minutes=60,
                subject=subject,
                topic="Тема урока",
                rate_cents=200_000,
                paid=paid,
            )
        )

    mathematics = service.search(
        LessonJournalFilter(
            query="математика",
            subject="mathematics",
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 8),
            time_from_minute=17 * 60,
            time_to_minute=19 * 60,
            limit=1,
        ),
        now=datetime(2026, 8, 8, 20),
    )
    assert mathematics.total == 2
    assert mathematics.has_more
    assert mathematics.summary.lessons == 2
    assert mathematics.summary.students == 1
    assert mathematics.summary.paid_cents == 200_000
    assert mathematics.summary.unpaid_cents == 200_000

    debt = service.search(
        LessonJournalFilter(
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 8),
            payment="unpaid_past",
        ),
        now=datetime(2026, 8, 8, 20),
    )
    assert {row.lesson.student_id for row in debt.rows} == {"anna", "xenia"}


def test_lesson_store_metadata_is_exposed_for_filters(store: CrmStore) -> None:
    lesson = ScheduledLesson(
        student_id="anna",
        student_name="Анна",
        starts_at=datetime(2026, 8, 6, 18, 0),
        duration_minutes=60,
        subject="mathematics",
        lesson_id="lesson-123",
    )
    store.save_one_off(lesson)
    stored_lesson = SimpleNamespace(
        lesson_id="lesson-123",
        source_audio_local="C:/audio.wav",
        artifacts=SimpleNamespace(
            verified_transcript="C:/transcript.txt",
            pdf="C:/handout.pdf",
        ),
        publication=None,
        status=SimpleNamespace(value="completed"),
    )

    class FakeLessonStore:
        def list(self, *, limit: int = 1000):
            return [stored_lesson]

        def get(self, lesson_id: str):
            return stored_lesson if lesson_id == "lesson-123" else None

    service = LessonJournalService(store, FakeLessonStore())
    result = service.search(
        LessonJournalFilter(
            date_from=date(2026, 8, 6),
            date_to=date(2026, 8, 6),
            recording=True,
            transcript=True,
            materials=True,
            processing_status="completed",
        )
    )
    assert result.total == 1
    assert result.rows[0].recording_exists
    assert result.rows[0].transcript_exists
    assert result.rows[0].materials_exist
