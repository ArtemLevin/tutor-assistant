from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from tutor_assistant.crm import CrmStore, ScheduleConflict, ScheduledLesson, ScheduleRule
from tutor_assistant.domain import Student
from tutor_assistant.schedule_status import (
    ScheduledLessonStatus,
    set_scheduled_lesson_status,
    summarize_schedule,
)


def _recurring_lesson(store: CrmStore, week_start: date):
    store.sync_students([Student(id="cancel-test", full_name="Тест отмен")])
    if not store.list_schedule_rules():
        store.save_schedule_rule(
            ScheduleRule(
                student_id="cancel-test",
                weekday=2,
                start_minute=16 * 60,
                duration_minutes=60,
                subject="mathematics",
                topic="Параметры",
                valid_from=date(2026, 8, 1),
                rate_cents=300_000,
            )
        )
    return store.lessons_for_week(week_start)[0]


def test_cancelled_recurring_lesson_is_accounted_without_deleting_series(tmp_path) -> None:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    week = date(2026, 8, 3)
    lesson = _recurring_lesson(store, week)

    occurrence_id = set_scheduled_lesson_status(
        store,
        lesson,
        ScheduledLessonStatus.CANCELLED,
    )

    cancelled = store.lessons_for_week(week)
    assert len(cancelled) == 1
    assert cancelled[0].occurrence_id == occurrence_id
    assert cancelled[0].status == ScheduledLessonStatus.CANCELLED.value
    summary = summarize_schedule(cancelled)
    assert summary.active_lessons == 0
    assert summary.cancelled_lessons == 1
    assert summary.total_lessons == 1
    assert summary.planned_revenue_cents == 0
    legacy_stats = store.stats(week)
    assert legacy_stats.lessons_this_week == 0
    assert legacy_stats.planned_revenue_cents == 0

    following = store.lessons_for_week(date(2026, 8, 10))
    assert len(following) == 1
    assert following[0].status == ScheduledLessonStatus.PLANNED.value
    assert following[0].occurrence_id is None


def test_cancelled_lesson_can_be_restored_on_same_occurrence(tmp_path) -> None:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    week = date(2026, 8, 3)
    lesson = _recurring_lesson(store, week)
    occurrence_id = set_scheduled_lesson_status(store, lesson, ScheduledLessonStatus.CANCELLED)
    cancelled = store.lessons_for_week(week)[0]

    restored_id = set_scheduled_lesson_status(store, cancelled, ScheduledLessonStatus.PLANNED)

    restored = store.lessons_for_week(week)[0]
    assert restored_id == occurrence_id
    assert restored.occurrence_id == occurrence_id
    assert restored.status == ScheduledLessonStatus.PLANNED.value
    assert summarize_schedule([restored]).planned_revenue_cents == 300_000


def test_cancelled_lesson_cannot_be_restored_after_slot_was_reused(tmp_path) -> None:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    week = date(2026, 8, 3)
    lesson = _recurring_lesson(store, week)
    set_scheduled_lesson_status(store, lesson, ScheduledLessonStatus.CANCELLED)
    cancelled = store.lessons_for_week(week)[0]
    store.sync_students([Student(id="replacement", full_name="Замена")])
    store.save_one_off(
        ScheduledLesson(
            student_id="replacement",
            student_name="Замена",
            starts_at=cancelled.starts_at,
            duration_minutes=cancelled.duration_minutes,
            subject="mathematics",
            topic="Другое занятие",
        )
    )

    with pytest.raises(ScheduleConflict):
        set_scheduled_lesson_status(store, cancelled, ScheduledLessonStatus.PLANNED)

    lessons = store.lessons_for_week(week)
    original = next(item for item in lessons if item.student_id == "cancel-test")
    replacement = next(item for item in lessons if item.student_id == "replacement")
    assert original.status == ScheduledLessonStatus.CANCELLED.value
    assert replacement.status == ScheduledLessonStatus.PLANNED.value


def test_concurrent_cancellation_materializes_only_one_rule_exception(tmp_path) -> None:
    path = tmp_path / "assistant.sqlite3"
    first = CrmStore(path)
    lesson = _recurring_lesson(first, date(2026, 8, 3))
    second = CrmStore(path)

    def cancel(store: CrmStore) -> int:
        return set_scheduled_lesson_status(store, lesson, ScheduledLessonStatus.CANCELLED)

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(cancel, (first, second)))

    assert ids[0] == ids[1]
    with sqlite3.connect(path) as db:
        count = db.execute(
            "SELECT COUNT(*) FROM crm_lesson_occurrences WHERE rule_id=? AND original_date=?",
            (lesson.rule_id, lesson.original_date.isoformat()),
        ).fetchone()[0]
    assert count == 1
