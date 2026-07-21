import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from tutor_assistant.crm import (
    CrmStore,
    Guardian,
    ScheduleConflict,
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
        full_name="Николь Саркисянц",
        grade=11,
        exam="ЕГЭ",
        subjects=["mathematics"],
    )
    store.sync_students([source])
    profile = store.get_student("nikol")
    assert profile is not None
    profile.goal = "90+ баллов"
    store.save_student(profile, [])

    store.sync_students([source.model_copy(update={"full_name": "Николь С."})])

    updated = store.get_student("nikol")
    assert updated is not None
    assert updated.full_name == "Николь С."
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
