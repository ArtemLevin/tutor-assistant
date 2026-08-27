from __future__ import annotations

from datetime import date, datetime

import pytest

from tutor_assistant.crm import CrmStore, ScheduledLesson
from tutor_assistant.domain import Student
from tutor_assistant.lesson_journal import LessonJournalService
from tutor_assistant.schedule_status import (
    ScheduledLessonStatus,
    delete_one_off_lesson,
)


def _store(tmp_path) -> CrmStore:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    store.sync_students([Student(id="one-off", full_name="Разовое")])
    return store


def _one_off(store: CrmStore) -> ScheduledLesson:
    store.save_one_off(
        ScheduledLesson(
            student_id="one-off",
            student_name="Разовое",
            starts_at=datetime(2026, 8, 5, 17, 0),
            duration_minutes=60,
            subject="mathematics",
            topic="Ошибочная запись",
            rate_cents=250_000,
        )
    )
    return store.lessons_for_week(date(2026, 8, 3))[0]


def test_one_off_schedule_record_can_be_physically_deleted_with_dependent_metadata(tmp_path) -> None:
    store = _store(tmp_path)
    lesson = _one_off(store)
    store.set_lesson_paid(lesson, True)
    journal = LessonJournalService(store)
    journal.set_homework_due(lesson, datetime(2026, 8, 7, 20, 0))

    delete_one_off_lesson(store, lesson)

    assert store.lessons_for_week(date(2026, 8, 3)) == []
    with store.connect() as db:
        occurrences = db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0]
        homework = db.execute("SELECT COUNT(*) FROM crm_lesson_homework").fetchone()[0]
    assert occurrences == 0
    assert homework == 0


def test_completed_one_off_schedule_record_cannot_be_physically_deleted(tmp_path) -> None:
    store = _store(tmp_path)
    lesson = _one_off(store)
    assert lesson.occurrence_id is not None
    store.update_occurrence(
        lesson.occurrence_id,
        status=ScheduledLessonStatus.COMPLETED.value,
        lesson_id="lesson-completed",
    )
    completed = store.lessons_for_week(date(2026, 8, 3))[0]

    with pytest.raises(ValueError, match="нельзя удалить"):
        delete_one_off_lesson(store, completed)

    remaining = store.lessons_for_week(date(2026, 8, 3))
    assert len(remaining) == 1
    assert remaining[0].status == ScheduledLessonStatus.COMPLETED.value
    assert remaining[0].lesson_id == "lesson-completed"
