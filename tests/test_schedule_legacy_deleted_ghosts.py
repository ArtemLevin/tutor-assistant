from __future__ import annotations

from datetime import date
from pathlib import Path

from tutor_assistant.crm import CrmStore, ScheduleRule
from tutor_assistant.domain import Student
from tutor_assistant.schedule_status import ScheduledLessonStatus


def _store(path: Path) -> CrmStore:
    store = CrmStore(path)
    store.sync_students(
        [
            Student(id="legacy", full_name="Legacy"),
            Student(id="replacement", full_name="Replacement"),
        ]
    )
    return store


def _series(store: CrmStore) -> int:
    return store.save_schedule_rule(
        ScheduleRule(
            student_id="legacy",
            weekday=2,
            start_minute=16 * 60,
            duration_minutes=60,
            subject="mathematics",
            valid_from=date(2026, 8, 1),
            rate_cents=300_000,
        )
    )


def test_legacy_deleted_series_ghost_is_cancelled_on_reopen_and_slot_is_reusable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "assistant.sqlite3"
    store = _store(path)
    rule_id = _series(store)
    future = store.lessons_for_week(date(2026, 8, 17))[0]
    occurrence_id = store.set_lesson_paid(future, True)

    # Old builds deleted a series by setting active=0 only. A materialized future
    # occurrence could therefore survive as a hidden planned concrete booking.
    with store.connect() as db:
        db.execute(
            "UPDATE crm_schedule_rules SET active=0, ended_from=NULL WHERE id=?",
            (rule_id,),
        )
        ghost = db.execute(
            "SELECT status, paid FROM crm_lesson_occurrences WHERE id=?",
            (occurrence_id,),
        ).fetchone()
    assert ghost is not None
    assert ghost["status"] == ScheduledLessonStatus.PLANNED.value
    assert ghost["paid"] == 1
    assert store.lessons_for_week(date(2026, 8, 10)) == []

    reopened = CrmStore(path)
    with reopened.connect() as db:
        repaired = db.execute(
            "SELECT id, status, paid FROM crm_lesson_occurrences WHERE id=?",
            (occurrence_id,),
        ).fetchone()
    assert repaired is not None
    assert repaired["id"] == occurrence_id
    assert repaired["status"] == ScheduledLessonStatus.CANCELLED.value
    assert repaired["paid"] == 1

    replacement_id = reopened.save_schedule_rule(
        ScheduleRule(
            student_id="replacement",
            weekday=2,
            start_minute=16 * 60,
            duration_minutes=60,
            subject="mathematics",
            valid_from=date(2026, 8, 12),
            rate_cents=310_000,
        )
    )
    assert replacement_id != rule_id
    week = reopened.lessons_for_week(date(2026, 8, 17))
    active = [
        item for item in week if item.status != ScheduledLessonStatus.CANCELLED.value
    ]
    assert len(active) == 1
    assert active[0].rule_id == replacement_id


def test_legacy_deleted_series_repair_preserves_linked_lesson_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "assistant.sqlite3"
    store = _store(path)
    rule_id = _series(store)
    lesson = store.lessons_for_week(date(2026, 8, 17))[0]
    occurrence_id = store.ensure_occurrence(lesson)
    store.update_occurrence(
        occurrence_id,
        status=ScheduledLessonStatus.COMPLETED.value,
        lesson_id="lesson-history",
    )
    with store.connect() as db:
        db.execute(
            "UPDATE crm_schedule_rules SET active=0, ended_from=NULL WHERE id=?",
            (rule_id,),
        )

    reopened = CrmStore(path)
    with reopened.connect() as db:
        persisted = db.execute(
            "SELECT status, lesson_id FROM crm_lesson_occurrences WHERE id=?",
            (occurrence_id,),
        ).fetchone()
    assert persisted is not None
    assert persisted["status"] == ScheduledLessonStatus.COMPLETED.value
    assert persisted["lesson_id"] == "lesson-history"
