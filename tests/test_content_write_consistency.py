from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import tutor_assistant.atomic_io as atomic_io
import tutor_assistant.content.service as content_service_module
from tutor_assistant.config import AppConfig
from tutor_assistant.content import LessonEditConflictError, StudentContentService
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.pipeline import LessonPipeline
from tutor_assistant.publisher import PublicationResult


def make_lesson(lesson_id: str = "write-consistency") -> Lesson:
    return Lesson(
        lesson_id=lesson_id,
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 18),
        topic="Исходная тема",
    )


def test_atomic_write_retries_windows_lock_and_cleans_unique_temp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "lesson.json"
    target.write_text('{"topic":"old"}', encoding="utf-8")
    real_replace = Path.replace
    calls = 0

    def flaky_replace(source: Path, destination: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(5, "Отказано в доступе", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(atomic_io, "sleep", lambda _seconds: None)

    atomic_io.atomic_write_text(target, '{"topic":"new"}')

    assert calls == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"topic": "new"}
    assert not list(tmp_path.glob(".lesson.json.*.tmp"))


def test_atomic_write_uses_durable_fallback_when_target_stays_locked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "transcript.txt"

    def locked_replace(_source: Path, destination: Path) -> Path:
        raise PermissionError(5, "Отказано в доступе", str(destination))

    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr(atomic_io, "sleep", lambda _seconds: None)
    monkeypatch.setattr(atomic_io, "ATOMIC_WRITE_ATTEMPTS", 2)

    atomic_io.atomic_write_text(target, "Надёжный текст\n")

    assert target.read_text(encoding="utf-8") == "Надёжный текст\n"
    assert not list(tmp_path.glob(".transcript.txt.*.tmp"))


def test_pipeline_status_update_preserves_concurrent_archive_edit(tmp_path: Path) -> None:
    config = AppConfig(workspace=tmp_path / "data")
    pipeline = LessonPipeline(config)
    stale = make_lesson()
    pipeline.create(stale)

    pipeline.content_service.update_lesson_metadata(
        stale.lesson_id,
        student=stale.student,
        subject=stale.subject,
        lesson_date=stale.lesson_date,
        topic="Отредактированная тема",
        expected_updated_at=stale.updated_at,
    )
    stale.transition(JobStatus.RECORDING)
    pipeline.save_state(stale, "status", "error")

    stored = pipeline.content_service.get_lesson(stale.lesson_id).lesson
    disk = Lesson.read_json(pipeline.lesson_dir(stored) / "lesson.json")
    assert stored.topic == disk.topic == "Отредактированная тема"
    assert stored.status == disk.status == JobStatus.RECORDING


def test_pipeline_compare_and_swap_rejects_stale_row_version(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    lesson = service.create_lesson(make_lesson("cas"))
    version = service.repository.lesson_row_version(lesson.lesson_id)
    lesson.transition(JobStatus.RECORDING)

    service.persist_pipeline_lesson(
        lesson,
        frozenset({"status", "error"}),
        expected_row_version=version,
    )
    lesson.transition(JobStatus.RECORDED)

    with pytest.raises(LessonEditConflictError, match="другим процессом"):
        service.persist_pipeline_lesson(
            lesson,
            frozenset({"status", "error"}),
            expected_row_version=version,
        )


def test_full_service_update_also_requires_current_row_version(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    stale = service.create_lesson(make_lesson("full-cas"))
    version = service.get_lesson(stale.lesson_id).row_version
    service.update_lesson_metadata(
        stale.lesson_id,
        student=stale.student,
        subject=stale.subject,
        lesson_date=stale.lesson_date,
        topic="Свежая карточка",
        expected_updated_at=stale.updated_at,
        expected_row_version=version,
    )
    stale.topic = "Устаревшая карточка"

    with pytest.raises(LessonEditConflictError, match="другим процессом"):
        service.update_lesson(stale, expected_row_version=version)

    assert service.get_lesson(stale.lesson_id).lesson.topic == "Свежая карточка"


def test_publication_uses_fresh_metadata_and_does_not_restore_stale_payload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path / "data"))
    stale = make_lesson("publication")
    pipeline.create(stale)
    pipeline.content_service.update_lesson_metadata(
        stale.lesson_id,
        student=stale.student,
        subject=stale.subject,
        lesson_date=stale.lesson_date,
        topic="Свежая тема публикации",
        expected_updated_at=stale.updated_at,
    )
    published_topics: list[str] = []

    def fake_publish(_publisher, lesson: Lesson, _directory: Path) -> PublicationResult:
        published_topics.append(lesson.topic)
        return PublicationResult(
            branch="lesson/publication",
            repository_path="students/student/publication",
            commit="abc123",
        )

    monkeypatch.setattr("tutor_assistant.pipeline.LessonPublisher.publish", fake_publish)

    pipeline.publish(stale)

    stored = pipeline.content_service.get_lesson(stale.lesson_id).lesson
    assert published_topics == ["Свежая тема публикации"]
    assert stored.topic == "Свежая тема публикации"
    assert stored.publication is not None
    assert stored.publication.commit == "abc123"


def test_pipeline_approval_uses_content_revision_writer(tmp_path: Path) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path / "data"))
    lesson = make_lesson("approval")
    transcript = pipeline.lesson_dir(lesson) / "transcript" / "transcript_verified.txt"
    lesson.artifacts.verified_transcript = str(transcript.resolve())
    lesson.status = JobStatus.REVIEW_REQUIRED
    pipeline.create(lesson)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("Черновой текст\n", encoding="utf-8")

    pipeline.approve_transcript(lesson, "Подтверждённый текст")

    content = pipeline.content_service.get_lesson(lesson.lesson_id)
    assert content.lesson.status == JobStatus.READY
    assert content.transcript is not None
    assert content.transcript.content == "Подтверждённый текст\n"
    assert transcript.read_text(encoding="utf-8") == "Подтверждённый текст\n"


def test_publication_rejects_metadata_changed_during_external_operation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path / "data"))
    lesson = make_lesson("publication-cas")
    pipeline.create(lesson)

    def concurrent_publish(_publisher, current: Lesson, _directory: Path) -> PublicationResult:
        pipeline.content_service.update_lesson_metadata(
            current.lesson_id,
            student=current.student,
            subject=current.subject,
            lesson_date=current.lesson_date,
            topic="Изменено во время публикации",
            expected_updated_at=current.updated_at,
        )
        return PublicationResult(
            branch="lesson/publication-cas",
            repository_path="students/student/publication-cas",
            commit="def456",
        )

    monkeypatch.setattr(
        "tutor_assistant.pipeline.LessonPublisher.publish",
        concurrent_publish,
    )

    with pytest.raises(LessonEditConflictError, match="другим процессом"):
        pipeline.publish(lesson)

    stored = pipeline.content_service.get_lesson(lesson.lesson_id).lesson
    assert stored.topic == "Изменено во время публикации"
    assert stored.publication is None


def test_pending_file_sync_is_recovered_from_sqlite(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    lesson = service.create_lesson(make_lesson("recover-files"))

    def locked_write(_path: Path, _content: str) -> None:
        raise PermissionError(5, "Отказано в доступе")

    with monkeypatch.context() as context:
        context.setattr(content_service_module, "atomic_write_text", locked_write)
        with pytest.raises(PermissionError):
            service.update_lesson_metadata(
                lesson.lesson_id,
                student=lesson.student,
                subject=lesson.subject,
                lesson_date=lesson.lesson_date,
                topic="SQLite уже обновлена",
                expected_updated_at=lesson.updated_at,
            )

    pending = service.repository.pending_file_sync()
    assert [item[0] for item in pending] == [lesson.lesson_id]
    assert "Отказано в доступе" in str(pending[0][1])
    assert Lesson.read_json(workspace / "lessons" / lesson.lesson_id / "lesson.json").topic == (
        "Исходная тема"
    )

    recovered = StudentContentService(workspace)

    assert recovered.repository.pending_file_sync() == []
    assert recovered.get_lesson(lesson.lesson_id).lesson.topic == "SQLite уже обновлена"
    assert Lesson.read_json(workspace / "lessons" / lesson.lesson_id / "lesson.json").topic == (
        "SQLite уже обновлена"
    )
