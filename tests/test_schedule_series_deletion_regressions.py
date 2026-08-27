from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pytest

from tutor_assistant.crm import CrmStore, ScheduleConflict, ScheduledLesson, ScheduleRule
from tutor_assistant.domain import Student
from tutor_assistant.lesson_journal import LessonJournalFilter, LessonJournalService
from tutor_assistant.schedule_status import ScheduledLessonStatus, set_scheduled_lesson_status


def _store(tmp_path) -> CrmStore:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    store.sync_students(
        [
            Student(id="series", full_name="Серия"),
            Student(id="one-off", full_name="Разовое"),
        ]
    )
    return store


def _save_series(
    store: CrmStore,
    *,
    valid_from: date = date(2026, 8, 1),
    valid_until: date | None = None,
) -> int:
    return store.save_schedule_rule(
        ScheduleRule(
            student_id="series",
            weekday=2,
            start_minute=16 * 60,
            duration_minutes=60,
            subject="mathematics",
            topic="Серия",
            valid_from=valid_from,
            valid_until=valid_until,
            rate_cents=300_000,
        )
    )


def test_schema_upgrade_adds_ended_from_marker_to_existing_schedule_table(tmp_path) -> None:
    db_path = tmp_path / "assistant.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE crm_schedule_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
                start_minute INTEGER NOT NULL CHECK(start_minute BETWEEN 0 AND 1439),
                duration_minutes INTEGER NOT NULL,
                subject TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                meeting_secret TEXT,
                valid_from TEXT NOT NULL,
                valid_until TEXT,
                rate_cents INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    store = CrmStore(db_path)

    with store.connect() as db:
        columns = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(crm_schedule_rules)")
        }
    assert "ended_from" in columns


def test_end_series_preserves_virtual_history_and_stops_selected_date_forward(tmp_path) -> None:
    store = _store(tmp_path)
    rule_id = _save_series(store)

    assert store.lessons_for_week(date(2026, 8, 3))[0].starts_at.date() == date(2026, 8, 5)
    assert store.lessons_for_week(date(2026, 8, 10))[0].starts_at.date() == date(2026, 8, 12)

    store.end_schedule_rule(rule_id, effective_from=date(2026, 8, 12))

    historical = store.lessons_for_week(date(2026, 8, 3))
    future = store.lessons_for_week(date(2026, 8, 10))
    rule = store.get_schedule_rule(rule_id)
    assert len(historical) == 1
    assert historical[0].starts_at.date() == date(2026, 8, 5)
    assert historical[0].status == ScheduledLessonStatus.PLANNED.value
    assert future == []
    assert rule is not None
    assert rule.active is False
    assert rule.valid_until == date(2026, 8, 11)
    with store.connect() as db:
        row = db.execute(
            "SELECT ended_from FROM crm_schedule_rules WHERE id=?",
            (rule_id,),
        ).fetchone()
    assert row is not None
    assert row["ended_from"] == "2026-08-12"


def test_legacy_inactive_rule_with_valid_until_does_not_reappear(tmp_path) -> None:
    store = _store(tmp_path)
    legacy_rule_id = _save_series(store, valid_until=date(2026, 8, 31))
    with store.connect() as db:
        db.execute(
            """
            UPDATE crm_schedule_rules
            SET active=0, ended_from=NULL
            WHERE id=?
            """,
            (legacy_rule_id,),
        )

    assert store.lessons_for_week(date(2026, 8, 3)) == []

    replacement_rule_id = _save_series(store, valid_until=date(2026, 8, 31))
    assert replacement_rule_id != legacy_rule_id


def test_ended_series_cannot_be_mutated_through_store_save(tmp_path) -> None:
    store = _store(tmp_path)
    rule_id = _save_series(store)
    store.end_schedule_rule(rule_id, effective_from=date(2026, 8, 12))
    rule = store.get_schedule_rule(rule_id)
    assert rule is not None

    with pytest.raises(ValueError, match="Завершённую серию нельзя изменять"):
        store.save_schedule_rule(
            rule.model_copy(
                update={
                    "topic": "Изменено",
                    "valid_until": date(2026, 8, 31),
                }
            )
        )
    with pytest.raises(ValueError, match="Завершённую серию нельзя изменять"):
        store.save_schedule_rule(rule.model_copy(update={"active": True}))

    stored = store.get_schedule_rule(rule_id)
    assert stored is not None
    assert stored.active is False
    assert stored.topic == "Серия"
    assert stored.valid_until == date(2026, 8, 11)
    assert store.lessons_for_week(date(2026, 8, 17)) == []


def test_end_series_cancels_future_paid_occurrence_without_losing_payment(tmp_path) -> None:
    store = _store(tmp_path)
    rule_id = _save_series(store)
    lesson = store.lessons_for_week(date(2026, 8, 10))[0]
    occurrence_id = store.set_lesson_paid(lesson, True)

    store.end_schedule_rule(rule_id, effective_from=date(2026, 8, 12))

    lessons = store.lessons_for_week(date(2026, 8, 10))
    assert len(lessons) == 1
    assert lessons[0].occurrence_id == occurrence_id
    assert lessons[0].status == ScheduledLessonStatus.CANCELLED.value
    assert lessons[0].paid is True
    stats = store.stats(date(2026, 8, 10))
    assert stats.lessons_this_week == 0
    assert stats.planned_revenue_cents == 0


def test_end_series_preserves_materialized_homework_metadata(tmp_path) -> None:
    store = _store(tmp_path)
    rule_id = _save_series(store)
    lesson = store.lessons_for_week(date(2026, 8, 10))[0]
    journal = LessonJournalService(store)
    occurrence_id = journal.set_homework_due(lesson, datetime(2026, 8, 14, 20, 0))

    store.end_schedule_rule(rule_id, effective_from=date(2026, 8, 12))

    cancelled = store.lessons_for_week(date(2026, 8, 10))[0]
    assert cancelled.occurrence_id == occurrence_id
    assert cancelled.status == ScheduledLessonStatus.CANCELLED.value
    with store.connect() as db:
        row = db.execute(
            "SELECT due_at FROM crm_lesson_homework WHERE occurrence_id=?",
            (occurrence_id,),
        ).fetchone()
    assert row is not None
    assert row["due_at"] == "2026-08-14T20:00:00"


def test_journal_keeps_unmaterialized_history_after_series_end(tmp_path) -> None:
    store = _store(tmp_path)
    rule_id = _save_series(store)
    store.end_schedule_rule(rule_id, effective_from=date(2026, 8, 12))
    journal = LessonJournalService(store)

    result = journal.search(
        LessonJournalFilter(
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 11),
        ),
        now=datetime(2026, 8, 20, 12, 0),
    )

    assert any(row.lesson.starts_at.date() == date(2026, 8, 5) for row in result.rows)


def test_recurring_rule_conflicts_with_existing_one_off_occurrence(tmp_path) -> None:
    store = _store(tmp_path)
    store.save_one_off(
        ScheduledLesson(
            student_id="one-off",
            student_name="Разовое",
            starts_at=datetime(2026, 8, 5, 16, 0),
            duration_minutes=60,
            subject="mathematics",
        )
    )

    with pytest.raises(ScheduleConflict, match="конкретное занятие"):
        _save_series(store)


def test_same_time_recurring_rules_are_allowed_when_date_ranges_do_not_overlap(tmp_path) -> None:
    store = _store(tmp_path)
    first = _save_series(store, valid_until=date(2026, 8, 31))

    second = _save_series(store, valid_from=date(2026, 9, 1))

    assert first != second
    assert len(store.list_schedule_rules()) == 2


def test_new_rule_cannot_overlap_preserved_history_of_ended_series(tmp_path) -> None:
    store = _store(tmp_path)
    rule_id = _save_series(store)
    store.end_schedule_rule(rule_id, effective_from=date(2026, 8, 12))

    with pytest.raises(ScheduleConflict, match="повторяющееся занятие"):
        _save_series(
            store,
            valid_from=date(2026, 8, 1),
            valid_until=date(2026, 8, 11),
        )


def test_completed_occurrence_cannot_be_cancelled_as_future_lesson(tmp_path) -> None:
    store = _store(tmp_path)
    _save_series(store)
    lesson = store.lessons_for_week(date(2026, 8, 3))[0]
    occurrence_id = store.ensure_occurrence(lesson)
    store.update_occurrence(
        occurrence_id,
        status=ScheduledLessonStatus.COMPLETED.value,
        lesson_id="lesson-completed",
    )
    completed = store.lessons_for_week(date(2026, 8, 3))[0]

    with pytest.raises(ValueError, match="нельзя отменить"):
        set_scheduled_lesson_status(store, completed, ScheduledLessonStatus.CANCELLED)

    restored = store.lessons_for_week(date(2026, 8, 3))[0]
    assert restored.status == ScheduledLessonStatus.COMPLETED.value
    assert restored.lesson_id == "lesson-completed"


def test_future_occurrence_from_ended_series_cannot_be_restored(tmp_path) -> None:
    store = _store(tmp_path)
    rule_id = _save_series(store)
    lesson = store.lessons_for_week(date(2026, 8, 10))[0]
    store.set_lesson_paid(lesson, True)
    store.end_schedule_rule(rule_id, effective_from=date(2026, 8, 12))
    cancelled = store.lessons_for_week(date(2026, 8, 10))[0]

    with pytest.raises(ScheduleConflict, match="границей завершённой серии"):
        set_scheduled_lesson_status(store, cancelled, ScheduledLessonStatus.PLANNED)
