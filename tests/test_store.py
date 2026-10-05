from datetime import date

import pytest

from tutor_assistant.domain import Lesson, Student
from tutor_assistant.store import AutomaticPublicationJobConflictError, LessonStore


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


def test_store_persists_immutable_automatic_publication_intent(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)

    first = store.ensure_automatic_publication_job(
        lesson.lesson_id,
        1,
        "a" * 64,
        "students/student/transcript/04.10.26.txt",
    )
    second = store.ensure_automatic_publication_job(
        lesson.lesson_id,
        1,
        "a" * 64,
        "students/student/transcript/04.10.26.txt",
    )
    running = store.update_automatic_publication_job(
        lesson.lesson_id,
        "running",
        increment_attempts=True,
    )

    assert first == second
    assert running.status == "running"
    assert running.attempts == 1
    assert store.list_automatic_publication_jobs() == [running]


def test_store_rejects_publication_intent_payload_change(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    store.ensure_automatic_publication_job(
        lesson.lesson_id,
        1,
        "a" * 64,
        "students/student/transcript/04.10.26.txt",
    )

    with pytest.raises(AutomaticPublicationJobConflictError):
        store.ensure_automatic_publication_job(
            lesson.lesson_id,
            2,
            "b" * 64,
            "students/student/transcript/04.10.26.txt",
        )


def test_store_repairs_unpublished_automatic_publication_intent(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    store.ensure_automatic_publication_job(lesson.lesson_id, 1, "a" * 64, path)
    store.update_automatic_publication_job(
        lesson.lesson_id,
        "blocked",
        error="legacy empty transcript",
        increment_attempts=True,
    )

    repaired = store.repair_automatic_publication_job(
        lesson.lesson_id,
        expected_revision_number=1,
        expected_content_sha256="a" * 64,
        expected_repository_path=path,
        revision_number=2,
        content_sha256="b" * 64,
        repository_path=path,
    )

    assert repaired.revision_number == 2
    assert repaired.content_sha256 == "b" * 64
    assert repaired.status == "waiting"
    assert repaired.attempts == 0
    assert repaired.error is None
    assert repaired.next_attempt_at is None


@pytest.mark.parametrize("status", ["running", "published"])
def test_store_rejects_repair_after_publication_starts(tmp_path, status) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    store.ensure_automatic_publication_job(lesson.lesson_id, 1, "a" * 64, path)
    store.update_automatic_publication_job(lesson.lesson_id, status)

    with pytest.raises(AutomaticPublicationJobConflictError):
        store.repair_automatic_publication_job(
            lesson.lesson_id,
            expected_revision_number=1,
            expected_content_sha256="a" * 64,
            expected_repository_path=path,
            revision_number=2,
            content_sha256="b" * 64,
            repository_path=path,
        )


def test_store_repair_uses_compare_and_swap_for_immutable_payload(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    store.ensure_automatic_publication_job(lesson.lesson_id, 1, "a" * 64, path)

    with pytest.raises(AutomaticPublicationJobConflictError):
        store.repair_automatic_publication_job(
            lesson.lesson_id,
            expected_revision_number=1,
            expected_content_sha256="c" * 64,
            expected_repository_path=path,
            revision_number=2,
            content_sha256="b" * 64,
            repository_path=path,
        )

    stored = store.get_automatic_publication_job(lesson.lesson_id)
    assert stored is not None
    assert stored.revision_number == 1
    assert stored.content_sha256 == "a" * 64
