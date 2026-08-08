import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from tutor_assistant.crm import (
    CrmStore,
    Guardian,
    ScheduleConflict,
    ScheduledLesson,
    ScheduleRule,
    StudentProfile,
)
from tutor_assistant.domain import Student


class TestCodec:
    def encrypt(self, value: str | None) -> str | None:
        return None if value is None else "secret:" + value[::-1]

    def decrypt(self, value: str | None) -> str | None:
        return None if value is None else value.removeprefix("secret:")[::-1]


@pytest.fixture
def store(tmp_path: Path) -> CrmStore:
    return CrmStore(tmp_path / "assistant.sqlite3", TestCodec())


def test_students_yaml_is_migrated_without_overwriting_crm_fields(store: CrmStore) -> None:
    source = Student(
        id="nikol",
        full_name="Тестовая Ученица",
        grade=11,
        exam="ЕГЭ",
        subjects=["mathematics"],
    )
    store.sync_students([source])
    profile = store.get_student("nikol")
    assert profile is not None
    profile.goal = "90+ баллов"
    store.save_student(profile, [])

    store.sync_students([source.model_copy(update={"full_name": "Изменённое имя"})])

    updated = store.get_student("nikol")
    assert updated is not None
    assert updated.full_name == "Тестовая Ученица"
    assert updated.goal == "90+ баллов"


def test_guardian_contacts_and_notes_are_encrypted_at_rest(store: CrmStore) -> None:
    profile = StudentProfile(
        id="sofya",
        full_name="Софья Кальней",
        grade=9,
        goal="Подготовка к ОГЭ",
        notes="Предпочитает наглядные схемы",
        default_rate_cents=250_000,
    )
    guardian = Guardian(
        full_name="Анна Кальней",
        relationship="Мама",
        phone="+7 900 000-00-00",
        email="parent@example.com",
        social_url="https://t.me/example",
        is_primary=True,
    )

    store.save_student(profile, [guardian])

    loaded = store.get_student("sofya")
    contacts = store.list_guardians("sofya")
    assert loaded is not None and loaded.notes == profile.notes
    assert contacts[0].phone == guardian.phone
    assert contacts[0].social_url == guardian.social_url
    with sqlite3.connect(store.path) as db:
        raw_notes = db.execute(
            "SELECT notes_secret FROM crm_students WHERE id='sofya'"
        ).fetchone()[0]
        raw_phone = db.execute("SELECT phone_secret FROM crm_guardians").fetchone()[0]
    assert profile.notes not in raw_notes
    assert guardian.phone not in raw_phone


def test_weekly_rule_expands_into_requested_week(store: CrmStore) -> None:
    store.sync_students([Student(id="timofey", full_name="Тимофей")])
    rule_id = store.save_schedule_rule(
        ScheduleRule(
            student_id="timofey",
            weekday=2,
            start_minute=16 * 60,
            duration_minutes=90,
            subject="chemistry",
            topic="Алканы",
            valid_from=date(2026, 7, 1),
            rate_cents=300_000,
        )
    )

    lessons = store.lessons_for_week(date(2026, 7, 13))

    assert len(lessons) == 1
    assert lessons[0].rule_id == rule_id
    assert lessons[0].starts_at == datetime(2026, 7, 15, 16, 0)
    assert lessons[0].duration_minutes == 90


def test_materialized_occurrence_can_be_linked_to_recording(store: CrmStore) -> None:
    store.sync_students([Student(id="timofey", full_name="Тимофей")])
    store.save_schedule_rule(
        ScheduleRule(
            student_id="timofey",
            weekday=2,
            start_minute=16 * 60,
            valid_from=date(2026, 7, 1),
        )
    )
    lesson = store.lessons_for_week(date(2026, 7, 13))[0]

    occurrence_id = store.ensure_occurrence(lesson)
    store.update_occurrence(occurrence_id, status="completed", lesson_id="lesson-12345678")

    materialized = store.lessons_for_week(date(2026, 7, 13))[0]
    assert materialized.occurrence_id == occurrence_id
    assert materialized.status == "completed"
    assert materialized.lesson_id == "lesson-12345678"


def test_overlapping_weekly_rules_are_rejected(store: CrmStore) -> None:
    store.sync_students(
        [Student(id="first", full_name="Первый"), Student(id="second", full_name="Второй")]
    )
    store.save_schedule_rule(
        ScheduleRule(
            student_id="first",
            weekday=0,
            start_minute=16 * 60,
            duration_minutes=90,
            valid_from=date(2026, 7, 1),
        )
    )

    with pytest.raises(ScheduleConflict):
        store.save_schedule_rule(
            ScheduleRule(
                student_id="second",
                weekday=0,
                start_minute=17 * 60,
                duration_minutes=60,
                valid_from=date(2026, 7, 1),
            )
        )


def test_existing_crm_database_adds_paid_column(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            """
            CREATE TABLE crm_lesson_occurrences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER,
                original_date TEXT,
                student_id TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                subject TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                meeting_secret TEXT,
                status TEXT NOT NULL DEFAULT 'planned',
                rate_cents INTEGER NOT NULL DEFAULT 0,
                lesson_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(rule_id, original_date)
            )
            """
        )
        db.execute(
            """
            INSERT INTO crm_lesson_occurrences (
                student_id, starts_at, duration_minutes, subject,
                created_at, updated_at
            ) VALUES ('legacy', '2026-08-03T16:00:00', 60, 'mathematics', 'now', 'now')
            """
        )

    store = CrmStore(path, TestCodec())

    with sqlite3.connect(store.path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(crm_lesson_occurrences)")}
        paid = db.execute("SELECT paid FROM crm_lesson_occurrences").fetchone()[0]
    assert "paid" in columns
    assert paid == 0


def test_recurring_payment_materializes_only_selected_date(store: CrmStore) -> None:
    store.sync_students([Student(id="paid-student", full_name="Оплата")])
    store.save_schedule_rule(
        ScheduleRule(
            student_id="paid-student",
            weekday=1,
            start_minute=16 * 60,
            valid_from=date(2026, 8, 1),
            rate_cents=300_000,
        )
    )
    first_week = date(2026, 8, 3)
    lesson = store.lessons_for_week(first_week)[0]
    assert lesson.occurrence_id is None
    assert lesson.paid is False

    occurrence_id = store.set_lesson_paid(lesson, True)

    paid_lesson = store.lessons_for_week(first_week)[0]
    next_lesson = store.lessons_for_week(date(2026, 8, 10))[0]
    assert paid_lesson.occurrence_id == occurrence_id
    assert paid_lesson.paid is True
    assert next_lesson.occurrence_id is None
    assert next_lesson.paid is False
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0] == 1

    store.set_lesson_paid(paid_lesson, True)
    store.set_lesson_paid(paid_lesson, False)
    assert store.lessons_for_week(first_week)[0].paid is False
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0] == 1


def test_paid_state_survives_occurrence_detail_edit(store: CrmStore) -> None:
    store.sync_students([Student(id="one-off-paid", full_name="Разовое занятие")])
    week_start = date(2026, 8, 3)
    occurrence_id = store.save_one_off(
        ScheduledLesson(
            student_id="one-off-paid",
            student_name="Разовое занятие",
            starts_at=datetime(2026, 8, 5, 17, 0),
            duration_minutes=60,
            subject="mathematics",
            topic="Исходная тема",
        )
    )
    lesson = store.lessons_for_week(week_start)[0]
    store.set_lesson_paid(lesson, True)

    edited = lesson.model_copy(update={"topic": "Новая тема", "paid": True})
    store.update_occurrence_details(occurrence_id, edited)

    restored = store.lessons_for_week(week_start)[0]
    assert restored.topic == "Новая тема"
    assert restored.paid is True
