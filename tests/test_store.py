from datetime import date

import pytest

from tutor_assistant.domain import Lesson, Student
from tutor_assistant.store import LessonStore


def test_store_creates_lesson_but_rejects_legacy_updates(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 7, 12),
        topic="Резонанс",
    )
    store.save(lesson)
    lesson.topic = "Механический резонанс"
    with pytest.raises(RuntimeError, match="StudentContentService"):
        store.save(lesson)
    assert store.get(lesson.lesson_id).topic == "Резонанс"
    assert len(store.list()) == 1


def test_store_uses_wal_and_persists_transcription_job(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 7, 12),
        topic="Волны",
    )
    store.save(lesson)
    store.save_transcription_job(lesson.lesson_id, "lesson.wav", "waiting")

    with store.connect() as db:
        journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]

    assert journal_mode == "wal"
    assert store.list_transcription_jobs()[0].lesson_id == lesson.lesson_id
