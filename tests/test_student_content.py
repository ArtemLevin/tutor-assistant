from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tutor_assistant.content import (
    AssetKind,
    ContentConflictError,
    ContentPathError,
    LessonFilters,
    StudentContentRepository,
    StudentContentService,
)
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.store import LessonStore


def make_lesson(
    lesson_id: str = "lesson-one",
    *,
    student_id: str = "student",
    subject: str = "mathematics",
    lesson_date: date = date(2026, 7, 18),
    topic: str = "Квадратные уравнения",
) -> Lesson:
    return Lesson(
        lesson_id=lesson_id,
        student=Student(id=student_id, full_name=f"Ученик {student_id}"),
        subject=subject,
        lesson_date=lesson_date,
        topic=topic,
    )


def test_migrates_legacy_database_without_losing_lessons(tmp_path: Path) -> None:
    database = tmp_path / "tutor-assistant.sqlite3"
    lesson = make_lesson()
    with sqlite3.connect(database) as db:
        db.execute(
            """
            CREATE TABLE lessons (
                lesson_id TEXT PRIMARY KEY,
                student_id TEXT NOT NULL,
                lesson_date TEXT NOT NULL,
                topic TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            "INSERT INTO lessons VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                lesson.lesson_id,
                lesson.student.id,
                lesson.lesson_date.isoformat(),
                lesson.topic,
                lesson.status.value,
                lesson.model_dump_json(),
                lesson.updated_at.isoformat(),
            ),
        )

    LessonStore(database)
    repository = StudentContentRepository(database)

    assert repository.get_lesson(lesson.lesson_id) == lesson
    assert repository.applied_migrations() == [
        (1, "student_content_domain"),
        (2, "student_content_indexes"),
        (3, "student_content_editing"),
        (4, "student_content_trash"),
        (5, "student_content_hardening"),
        (6, "content_write_consistency"),
        (7, "asset_verification_cache"),
    ]
    with repository.connect() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(lessons)")}
    assert {"subject", "created_at", "deleted_at", "row_version"} <= columns
    with repository.connect() as db:
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_file_sync'"
        ).fetchone()


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "tutor-assistant.sqlite3"
    StudentContentRepository(database)
    repository = StudentContentRepository(database)

    assert len(repository.applied_migrations()) == 7
    with repository.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 7


def test_filters_pagination_and_lesson_soft_delete(tmp_path: Path) -> None:
    repository = StudentContentRepository(tmp_path / "content.sqlite3")
    repository.upsert_lesson(make_lesson("math-new"))
    repository.upsert_lesson(
        make_lesson(
            "math-old",
            lesson_date=date(2026, 7, 10),
            topic="Линейные уравнения",
        )
    )
    repository.upsert_lesson(
        make_lesson(
            "chemistry",
            student_id="other",
            subject="chemistry",
            topic="Алканы",
        )
    )

    first_page = repository.list_lessons(LessonFilters(student_id="student", subject="mathematics", limit=1))
    assert first_page.total == 2
    assert [item.lesson_id for item in first_page.items] == ["math-new"]

    second_page = repository.list_lessons(
        LessonFilters(student_id="student", subject="mathematics", limit=1, offset=1)
    )
    assert [item.lesson_id for item in second_page.items] == ["math-old"]

    repository.set_lesson_deleted("math-new", deleted=True)
    assert repository.get_lesson("math-new") is None
    assert repository.list_lessons(LessonFilters(student_id="student")).total == 1
    assert repository.list_lessons(LessonFilters(student_id="student", include_deleted=True)).total == 2
    repository.set_lesson_deleted("math-new", deleted=False)
    assert repository.get_lesson("math-new") is not None


def test_asset_and_transcript_revision_crud(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    lesson = service.create_lesson(make_lesson())
    audio = workspace / "lessons" / lesson.lesson_id / "recording" / "lesson.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"RIFF-test-audio")

    asset = service.register_asset(lesson.lesson_id, audio, kind=AssetKind.AUDIO)
    assert asset.id is not None
    assert any(
        item.relative_path.endswith("lesson.wav") for item in service.get_lesson(lesson.lesson_id).assets
    )
    service.delete_asset(asset.id)
    assert all(
        not item.relative_path.endswith("lesson.wav") for item in service.get_lesson(lesson.lesson_id).assets
    )
    service.restore_asset(asset.id)
    assert any(
        item.relative_path.endswith("lesson.wav") for item in service.get_lesson(lesson.lesson_id).assets
    )

    first = service.save_transcript(lesson.lesson_id, "Первая версия")
    second = service.save_transcript(lesson.lesson_id, "Вторая версия")
    assert (first.revision_number, second.revision_number) == (1, 2)
    assert service.get_lesson(lesson.lesson_id).transcript.content == "Вторая версия\n"
    persisted_lesson = Lesson.read_json(workspace / "lessons" / lesson.lesson_id / "lesson.json")
    assert persisted_lesson.artifacts.verified_transcript.endswith("transcript_verified.txt")
    restored = service.revert_transcript(first.id)
    assert restored.revision_number == 3
    assert restored.content == "Первая версия\n"
    service.delete_transcript_revision(restored.id)
    assert service.get_lesson(lesson.lesson_id).transcript.revision_number == 2
    service.restore_transcript_revision(restored.id)
    assert service.get_lesson(lesson.lesson_id).transcript.revision_number == 3


def test_service_create_update_delete_and_path_boundary(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    lesson = service.create_lesson(make_lesson())
    with pytest.raises(ContentConflictError):
        service.create_lesson(lesson)

    lesson.topic = "Обновлённая тема"
    previous_updated_at = lesson.updated_at
    row_version = service.get_lesson(lesson.lesson_id).row_version
    service.update_lesson(lesson, expected_row_version=row_version)
    assert service.get_lesson(lesson.lesson_id).lesson.topic == "Обновлённая тема"
    assert lesson.updated_at >= previous_updated_at

    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"outside")
    with pytest.raises(ContentPathError):
        service.register_asset(lesson.lesson_id, outside, kind=AssetKind.AUDIO)
    with pytest.raises(ValueError):
        service.create_lesson(make_lesson("../escaped"))

    service.delete_lesson(lesson.lesson_id)
    assert service.list_lessons().total == 0
    assert service.get_lesson(lesson.lesson_id, include_deleted=True).deleted_at is not None
    service.restore_lesson(lesson.lesson_id)
    assert service.list_lessons().total == 1


def test_legacy_indexer_is_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    lesson = make_lesson("legacy")
    lesson_dir = workspace / "lessons" / lesson.lesson_id
    recording = lesson_dir / "recording" / "lesson.wav"
    transcript = lesson_dir / "transcript" / "03_content_only_medium.txt"
    recording.parent.mkdir(parents=True)
    transcript.parent.mkdir(parents=True)
    recording.write_bytes(b"RIFF-legacy")
    transcript.write_text("Существующий транскрипт", encoding="utf-8")
    (lesson_dir / "transcript" / "manifest.json").write_text("{}", encoding="utf-8")
    lesson.write_json(lesson_dir / "lesson.json")

    service = StudentContentService(workspace)
    first = service.index_existing_lessons()
    second = service.index_existing_lessons()

    assert first.errors == second.errors == []
    assert first.indexed_lessons == second.indexed_lessons == 1
    assert len(service.repository.list_assets(lesson.lesson_id)) == 4
    revisions = service.repository.list_transcript_revisions(lesson.lesson_id)
    assert len(revisions) == 1
    assert revisions[0].content == "Существующий транскрипт"


def test_repository_filter_supports_status_and_text_search(tmp_path: Path) -> None:
    repository = StudentContentRepository(tmp_path / "content.sqlite3")
    lesson = make_lesson(topic="Особая тема")
    lesson.status = JobStatus.REVIEW_REQUIRED
    lesson.updated_at = datetime.now(UTC)
    repository.upsert_lesson(lesson)

    page = repository.list_lessons(LessonFilters(status=JobStatus.REVIEW_REQUIRED, query="особая"))
    assert page.total == 1
    assert page.items[0].lesson_id == lesson.lesson_id
