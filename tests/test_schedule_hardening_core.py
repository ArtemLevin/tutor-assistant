from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import pytest

from tutor_assistant.crm import CrmStore, ScheduleConflict, ScheduledLesson, ScheduleRule
from tutor_assistant.domain import Student
from tutor_assistant.schedule_status import (
    ScheduledLessonStatus,
    set_scheduled_lesson_status,
)


def make_store(path: Path) -> CrmStore:
    store = CrmStore(path)
    store.sync_students(
        [
            Student(id="series", full_name="Серия"),
            Student(id="other", full_name="Другой"),
        ]
    )
    return store


def make_series(store: CrmStore) -> int:
    return store.save_schedule_rule(
        ScheduleRule(
            student_id="series",
            weekday=2,
            start_minute=16 * 60,
            duration_minutes=60,
            subject="mathematics",
            topic="Старая тема",
            meeting_url="https://old.example",
            valid_from=date(2026, 8, 1),
            rate_cents=300_000,
        )
    )


def test_cross_week_reschedule_suppresses_original_virtual_occurrence(tmp_path: Path) -> None:
    store = make_store(tmp_path / "assistant.sqlite3")
    make_series(store)
    source_week = date(2026, 8, 3)
    lesson = store.lessons_for_week(source_week)[0]
    occurrence_id = store.ensure_occurrence(lesson)

    moved = lesson.model_copy(
        update={
            "occurrence_id": occurrence_id,
            "starts_at": datetime(2026, 8, 13, 18, 0),
            "topic": "Перенос",
        }
    )
    store.update_occurrence_details(occurrence_id, moved)

    assert store.lessons_for_week(source_week) == []
    destination = store.lessons_for_week(date(2026, 8, 10))
    assert [(item.original_date, item.starts_at, item.topic) for item in destination] == [
        (date(2026, 8, 12), datetime(2026, 8, 12, 16, 0), "Старая тема"),
        (date(2026, 8, 5), datetime(2026, 8, 13, 18, 0), "Перенос"),
    ]


def test_series_replacement_preserves_history_and_materialized_metadata(tmp_path: Path) -> None:
    store = make_store(tmp_path / "assistant.sqlite3")
    old_rule_id = make_series(store)
    historical = store.lessons_for_week(date(2026, 8, 3))[0]
    future = store.lessons_for_week(date(2026, 8, 17))[0]
    future_occurrence_id = store.set_lesson_paid(future, True)

    new_rule_id = store.replace_schedule_rule_from(
        old_rule_id,
        ScheduleRule(
            student_id="series",
            weekday=2,
            start_minute=17 * 60,
            duration_minutes=90,
            subject="physics",
            topic="Новая тема",
            meeting_url="https://new.example",
            valid_from=date(2026, 8, 12),
            rate_cents=350_000,
        ),
        effective_from=date(2026, 8, 12),
    )

    assert new_rule_id != old_rule_id
    old_rule = store.get_schedule_rule(old_rule_id)
    new_rule = store.get_schedule_rule(new_rule_id)
    assert old_rule is not None and not old_rule.active
    assert old_rule.valid_until == date(2026, 8, 11)
    assert new_rule is not None and new_rule.active
    assert new_rule.valid_from == date(2026, 8, 12)

    restored_history = store.lessons_for_week(date(2026, 8, 3))[0]
    assert restored_history.starts_at == historical.starts_at
    assert restored_history.topic == "Старая тема"
    assert restored_history.rate_cents == 300_000

    changed = store.lessons_for_week(date(2026, 8, 10))[0]
    assert changed.rule_id == new_rule_id
    assert changed.starts_at == datetime(2026, 8, 12, 17, 0)
    assert changed.duration_minutes == 90
    assert changed.subject == "physics"
    assert changed.topic == "Новая тема"

    materialized = store.lessons_for_week(date(2026, 8, 17))[0]
    assert materialized.occurrence_id == future_occurrence_id
    assert materialized.rule_id == new_rule_id
    assert materialized.starts_at == datetime(2026, 8, 19, 17, 0)
    assert materialized.paid is True
    assert materialized.rate_cents == 350_000


def test_series_replacement_keeps_explicit_moved_exception(tmp_path: Path) -> None:
    store = make_store(tmp_path / "assistant.sqlite3")
    old_rule_id = make_series(store)
    lesson = store.lessons_for_week(date(2026, 8, 10))[0]
    occurrence_id = store.ensure_occurrence(lesson)
    moved = lesson.model_copy(
        update={
            "occurrence_id": occurrence_id,
            "starts_at": datetime(2026, 8, 14, 18, 0),
            "topic": "Индивидуальный перенос",
        }
    )
    store.update_occurrence_details(occurrence_id, moved)

    new_rule_id = store.replace_schedule_rule_from(
        old_rule_id,
        ScheduleRule(
            student_id="series",
            weekday=2,
            start_minute=17 * 60,
            duration_minutes=60,
            subject="mathematics",
            topic="Новая серия",
            valid_from=date(2026, 8, 12),
            rate_cents=310_000,
        ),
        effective_from=date(2026, 8, 12),
    )

    week = store.lessons_for_week(date(2026, 8, 10))
    assert len(week) == 1
    assert week[0].occurrence_id == occurrence_id
    assert week[0].rule_id == new_rule_id
    assert week[0].starts_at == datetime(2026, 8, 14, 18, 0)
    assert week[0].topic == "Индивидуальный перенос"


def test_concurrent_one_off_creation_cannot_double_book_slot(tmp_path: Path) -> None:
    path = tmp_path / "assistant.sqlite3"
    first = make_store(path)
    second = CrmStore(path)

    def save(store: CrmStore, student_id: str) -> str:
        try:
            store.save_one_off(
                ScheduledLesson(
                    student_id=student_id,
                    student_name=student_id,
                    starts_at=datetime(2026, 8, 5, 18, 0),
                    duration_minutes=60,
                    subject="mathematics",
                )
            )
        except ScheduleConflict:
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: save(*args), [(first, "series"), (second, "other")]))

    assert sorted(results) == ["conflict", "saved"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0] == 1


def test_inactive_legacy_series_occurrence_cannot_be_restored(tmp_path: Path) -> None:
    store = make_store(tmp_path / "assistant.sqlite3")
    rule_id = make_series(store)
    lesson = store.lessons_for_week(date(2026, 8, 3))[0]
    set_scheduled_lesson_status(store, lesson, ScheduledLessonStatus.CANCELLED)
    cancelled = store.lessons_for_week(date(2026, 8, 3))[0]
    with store.connect() as db:
        db.execute(
            "UPDATE crm_schedule_rules SET active=0, valid_until='2026-08-31', ended_from=NULL WHERE id=?",
            (rule_id,),
        )

    with pytest.raises(ScheduleConflict, match="границей завершённой серии"):
        set_scheduled_lesson_status(store, cancelled, ScheduledLessonStatus.PLANNED)


def test_recording_failed_lesson_cannot_be_cancelled(tmp_path: Path) -> None:
    store = make_store(tmp_path / "assistant.sqlite3")
    occurrence_id = store.save_one_off(
        ScheduledLesson(
            student_id="series",
            student_name="Серия",
            starts_at=datetime(2026, 8, 5, 18, 0),
            duration_minutes=60,
            subject="mathematics",
        )
    )
    store.update_occurrence(
        occurrence_id,
        status=ScheduledLessonStatus.RECORDING_FAILED.value,
        lesson_id="lesson-recording-failed",
    )
    failed = store.lessons_for_week(date(2026, 8, 3))[0]

    with pytest.raises(ValueError, match="связанное с записью"):
        set_scheduled_lesson_status(store, failed, ScheduledLessonStatus.CANCELLED)

    persisted = store.lessons_for_week(date(2026, 8, 3))[0]
    assert persisted.status == ScheduledLessonStatus.RECORDING_FAILED.value
    assert persisted.lesson_id == "lesson-recording-failed"


def test_restore_and_slot_reuse_are_serialized(tmp_path: Path) -> None:
    path = tmp_path / "assistant.sqlite3"
    first = make_store(path)
    make_series(first)
    lesson = first.lessons_for_week(date(2026, 8, 3))[0]
    set_scheduled_lesson_status(first, lesson, ScheduledLessonStatus.CANCELLED)
    cancelled = first.lessons_for_week(date(2026, 8, 3))[0]
    second = CrmStore(path)

    def restore() -> str:
        try:
            set_scheduled_lesson_status(first, cancelled, ScheduledLessonStatus.PLANNED)
        except ScheduleConflict:
            return "conflict"
        return "restored"

    def replace() -> str:
        try:
            second.save_one_off(
                ScheduledLesson(
                    student_id="other",
                    student_name="Другой",
                    starts_at=cancelled.starts_at,
                    duration_minutes=cancelled.duration_minutes,
                    subject="mathematics",
                )
            )
        except ScheduleConflict:
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        restore_future = pool.submit(restore)
        replace_future = pool.submit(replace)
        results = [restore_future.result(), replace_future.result()]

    assert results.count("conflict") == 1
    lessons = first.lessons_for_week(date(2026, 8, 3))
    active = [item for item in lessons if item.status != ScheduledLessonStatus.CANCELLED.value]
    assert len(active) == 1


def test_series_replacement_preserves_occurrence_foreign_key_metadata(tmp_path: Path) -> None:
    store = make_store(tmp_path / "assistant.sqlite3")
    old_rule_id = make_series(store)
    lesson = store.lessons_for_week(date(2026, 8, 17))[0]
    occurrence_id = store.ensure_occurrence(lesson)
    with store.connect() as db:
        db.execute(
            """
            CREATE TABLE crm_lesson_homework (
                occurrence_id INTEGER PRIMARY KEY,
                due_at TEXT,
                FOREIGN KEY(occurrence_id)
                    REFERENCES crm_lesson_occurrences(id) ON DELETE CASCADE
            )
            """
        )
        db.execute(
            "INSERT INTO crm_lesson_homework (occurrence_id, due_at) VALUES (?, ?)",
            (occurrence_id, "2026-08-21T20:00:00"),
        )

    new_rule_id = store.replace_schedule_rule_from(
        old_rule_id,
        ScheduleRule(
            student_id="series",
            weekday=2,
            start_minute=17 * 60,
            duration_minutes=60,
            subject="mathematics",
            topic="Новая серия",
            valid_from=date(2026, 8, 12),
            rate_cents=320_000,
        ),
        effective_from=date(2026, 8, 12),
    )

    with store.connect() as db:
        occurrence = db.execute(
            "SELECT id, rule_id FROM crm_lesson_occurrences WHERE id=?",
            (occurrence_id,),
        ).fetchone()
        homework = db.execute(
            "SELECT due_at FROM crm_lesson_homework WHERE occurrence_id=?",
            (occurrence_id,),
        ).fetchone()
    assert occurrence is not None
    assert occurrence["id"] == occurrence_id
    assert occurrence["rule_id"] == new_rule_id
    assert homework is not None
    assert homework["due_at"] == "2026-08-21T20:00:00"


def test_series_replacement_rejects_collision_with_moved_exception(tmp_path: Path) -> None:
    store = make_store(tmp_path / "assistant.sqlite3")
    old_rule_id = make_series(store)
    lesson = store.lessons_for_week(date(2026, 8, 3))[0]
    occurrence_id = store.ensure_occurrence(lesson)
    moved = lesson.model_copy(
        update={
            "occurrence_id": occurrence_id,
            "starts_at": datetime(2026, 8, 12, 17, 0),
        }
    )
    # The old series is at 16:00, so 17:00 on the following Wednesday is initially free.
    store.update_occurrence_details(occurrence_id, moved)

    with pytest.raises(ScheduleConflict, match="Перенесённое занятие"):
        store.replace_schedule_rule_from(
            old_rule_id,
            ScheduleRule(
                student_id="series",
                weekday=2,
                start_minute=17 * 60,
                duration_minutes=60,
                subject="mathematics",
                topic="Новая серия",
                valid_from=date(2026, 8, 5),
                rate_cents=320_000,
            ),
            effective_from=date(2026, 8, 5),
        )


def test_unchanged_series_save_does_not_fragment_rule_history(tmp_path: Path) -> None:
    store = make_store(tmp_path / "assistant.sqlite3")
    rule_id = make_series(store)
    current = store.get_schedule_rule(rule_id)
    assert current is not None

    returned = store.replace_schedule_rule_from(
        rule_id,
        current.model_copy(update={"id": None, "valid_from": date(2026, 8, 12)}),
        effective_from=date(2026, 8, 12),
    )

    assert returned == rule_id
    rules = store.list_schedule_rules()
    assert len(rules) == 1
    assert rules[0].id == rule_id
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM crm_schedule_rules").fetchone()[0] == 1
        ended_from = db.execute(
            "SELECT ended_from FROM crm_schedule_rules WHERE id=?", (rule_id,)
        ).fetchone()[0]
    assert ended_from is None
