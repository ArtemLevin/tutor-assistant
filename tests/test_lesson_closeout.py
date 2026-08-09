from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from tutor_assistant.crm import (
    CrmStore,
    PlainSecretCodec,
    ScheduledLesson,
    ScheduleRule,
    StudentProfile,
)
from tutor_assistant.lesson_closeout import AttendanceStatus, LessonCloseoutService


class TestCodec:
    def encrypt(self, value: str | None) -> str | None:
        return value

    def decrypt(self, value: str | None) -> str | None:
        return value


def _store(tmp_path: Path, name: str, codec=None) -> CrmStore:
    store = CrmStore(tmp_path / name, codec or TestCodec())
    store.save_student(
        StudentProfile(id="student", full_name="Ученик", subjects=["mathematics"]),
        [],
    )
    return store


def _lesson(starts_at: datetime) -> ScheduledLesson:
    return ScheduledLesson(
        student_id="student",
        student_name="Ученик",
        starts_at=starts_at,
        duration_minutes=60,
        subject="mathematics",
        topic="Производная",
        rate_cents=300_000,
    )


def test_closeout_schema_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path, "closeout-schema.sqlite3")
    LessonCloseoutService(store)
    LessonCloseoutService(store)

    with store.connect() as db:
        columns = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(crm_lesson_closeout)")
        }

    assert columns == {
        "occurrence_id",
        "attendance",
        "teacher_note_secret",
        "closed_at",
        "updated_at",
    }


def test_closeout_draft_preserves_utf8_note(tmp_path: Path) -> None:
    store = _store(tmp_path, "closeout-draft.sqlite3")
    lesson = _lesson(datetime.combine(date.today() - timedelta(days=1), time(16, 0)))
    store.save_one_off(lesson)
    service = LessonCloseoutService(store)

    service.save_draft(
        lesson,
        attendance=AttendanceStatus.LATE,
        teacher_note="Повторили производную. Ошибка: потерян знак −.",
    )

    meta = service.get_for_lesson(lesson)
    assert meta is not None
    assert meta.attendance == AttendanceStatus.LATE
    assert meta.teacher_note == "Повторили производную. Ошибка: потерян знак −."
    assert meta.closed_at is None


def test_close_lesson_updates_status_and_closeout_atomically(tmp_path: Path) -> None:
    store = _store(tmp_path, "closeout-complete.sqlite3")
    lesson = _lesson(datetime.combine(date.today() - timedelta(days=1), time(16, 0)))
    occurrence_id = store.save_one_off(lesson)
    service = LessonCloseoutService(store)

    service.close_lesson(
        lesson,
        attendance=AttendanceStatus.PRESENT,
        teacher_note="Тема усвоена уверенно",
    )

    with store.connect() as db:
        occurrence = db.execute(
            "SELECT status FROM crm_lesson_occurrences WHERE id=?",
            (occurrence_id,),
        ).fetchone()
        closeout = db.execute(
            "SELECT attendance, teacher_note_secret, closed_at FROM crm_lesson_closeout "
            "WHERE occurrence_id=?",
            (occurrence_id,),
        ).fetchone()

    assert occurrence["status"] == "completed"
    assert closeout["attendance"] == AttendanceStatus.PRESENT.value
    assert closeout["teacher_note_secret"] == "Тема усвоена уверенно"
    assert closeout["closed_at"]


def test_recurring_closeout_materializes_only_selected_date(tmp_path: Path) -> None:
    store = _store(tmp_path, "closeout-recurring.sqlite3")
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    rule_id = store.save_schedule_rule(
        ScheduleRule(
            student_id="student",
            weekday=0,
            start_minute=10 * 60,
            duration_minutes=60,
            subject="mathematics",
            valid_from=monday - timedelta(days=7),
        )
    )
    first = store.lessons_for_week(monday - timedelta(days=7))[0]
    second = store.lessons_for_week(monday)[0]
    service = LessonCloseoutService(store)

    service.close_lesson(
        first,
        attendance=AttendanceStatus.PRESENT,
        teacher_note="Закрыта только эта дата",
        at=first.ends_at + timedelta(hours=1),
    )

    with store.connect() as db:
        rows = db.execute(
            "SELECT rule_id, original_date FROM crm_lesson_occurrences WHERE rule_id=?",
            (rule_id,),
        ).fetchall()

    assert len(rows) == 1
    assert rows[0]["original_date"] == first.starts_at.date().isoformat()
    assert second.occurrence_id is None
    assert service.get_for_lesson(second) is None


def test_restore_snapshot_restores_status_and_exact_closeout(tmp_path: Path) -> None:
    store = _store(tmp_path, "closeout-undo.sqlite3")
    starts_at = datetime.combine(date.today() - timedelta(days=1), time(16, 0))
    lesson = _lesson(starts_at)
    store.save_one_off(lesson)
    service = LessonCloseoutService(store)
    draft_at = starts_at + timedelta(hours=2)
    service.save_draft(
        lesson,
        attendance=AttendanceStatus.LATE,
        teacher_note="Исходная заметка",
        at=draft_at,
    )
    snapshot = service.snapshot(lesson)

    service.close_lesson(
        lesson,
        attendance=AttendanceStatus.PRESENT,
        teacher_note="Изменённая заметка",
        at=starts_at + timedelta(hours=3),
    )
    service.restore_snapshot(lesson, snapshot, at=starts_at + timedelta(hours=4))

    restored = service.get_for_lesson(lesson)
    with store.connect() as db:
        status = db.execute(
            "SELECT status FROM crm_lesson_occurrences WHERE student_id=? AND starts_at=?",
            (lesson.student_id, lesson.starts_at.isoformat()),
        ).fetchone()["status"]

    assert status == "planned"
    assert restored is not None
    assert restored.attendance == AttendanceStatus.LATE
    assert restored.teacher_note == "Исходная заметка"
    assert restored.closed_at is None
    assert restored.updated_at == draft_at


def test_future_lesson_requires_waiting_for_end(tmp_path: Path) -> None:
    store = _store(tmp_path, "closeout-future.sqlite3")
    lesson = _lesson(datetime.now() + timedelta(days=1))
    service = LessonCloseoutService(store)

    with pytest.raises(ValueError, match="ещё не закончилось"):
        service.close_lesson(
            lesson,
            attendance=AttendanceStatus.PRESENT,
            teacher_note="",
        )


def test_teacher_note_stays_outside_scheduled_lesson_payload(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        "closeout-privacy.sqlite3",
        codec=PlainSecretCodec(),
    )
    lesson = _lesson(datetime.combine(date.today() - timedelta(days=1), time(16, 0)))
    store.save_one_off(lesson)
    service = LessonCloseoutService(store)
    service.close_lesson(
        lesson,
        attendance=AttendanceStatus.PRESENT,
        teacher_note="Локальная педагогическая заметка",
    )

    with store.connect() as db:
        secret = db.execute(
            "SELECT teacher_note_secret FROM crm_lesson_closeout"
        ).fetchone()["teacher_note_secret"]

    payload = lesson.model_dump()
    assert str(secret).startswith(PlainSecretCodec.prefix)
    assert "teacher_note" not in payload
    assert "attendance" not in payload
