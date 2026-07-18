from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tutor_assistant.content import (
    DuplicateImportError,
    ImportCancellationToken,
    ImportValidationError,
    LessonImportRequest,
    StudentContentService,
)
from tutor_assistant.domain import JobStatus, Student
from tutor_assistant.store import LessonStore
from tutor_assistant.transcription_queue import QueueStatus, TranscriptionQueue


def request(
    *,
    audio: Path | None = None,
    transcript: Path | None = None,
    enqueue: bool = False,
    lesson_id: str | None = None,
) -> LessonImportRequest:
    return LessonImportRequest(
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 18),
        topic="Импортированное занятие",
        audio_source=audio,
        transcript_source=transcript,
        enqueue_audio=enqueue,
        lesson_id=lesson_id,
    )


def test_audio_is_copied_to_managed_storage_and_survives_source_deletion(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFF-imported-audio")
    service = StudentContentService(tmp_path / "data")

    result = service.import_lesson(request(audio=source, enqueue=True, lesson_id="imported-audio"))
    source.unlink()

    assert result.cancelled is False
    assert result.enqueue_audio is True
    assert result.audio_path is not None
    assert result.audio_path.read_bytes() == b"RIFF-imported-audio"
    assert result.lesson is not None
    assert result.lesson.status == JobStatus.RECORDED
    content = service.get_lesson("imported-audio")
    assert content.lesson.source_audio_local == str(result.audio_path)
    assert {asset.kind.value for asset in content.assets} == {"audio", "metadata"}
    assert content.assets[0].sha256 == result.audio_sha256
    queue = TranscriptionQueue(LessonStore(service.repository.path))
    job = queue.enqueue(result.lesson, result.audio_path)
    assert job.status == QueueStatus.WAITING
    assert queue.start_next() is job
    assert job.audio.read_bytes() == b"RIFF-imported-audio"


def test_transcript_import_and_manual_lesson_creation(tmp_path: Path) -> None:
    source = tmp_path / "transcript.md"
    source.write_text("Готовый транскрипт", encoding="utf-8")
    service = StudentContentService(tmp_path / "data")

    imported = service.import_lesson(request(transcript=source, lesson_id="imported-transcript"))
    source.unlink()

    assert imported.lesson is not None
    assert imported.lesson.status == JobStatus.REVIEW_REQUIRED
    assert imported.transcript is not None
    assert imported.transcript.content == "Готовый транскрипт\n"
    transcript_path = Path(imported.lesson.artifacts.verified_transcript or "")
    assert transcript_path.read_text(encoding="utf-8") == "Готовый транскрипт\n"

    manual = service.import_lesson(request(lesson_id="manual-only"))
    assert manual.lesson is not None
    assert manual.lesson.status == JobStatus.DRAFT
    assert [asset.kind.value for asset in service.get_lesson("manual-only").assets] == ["metadata"]


def test_duplicate_audio_is_detected_by_sha256(tmp_path: Path) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.write_bytes(b"same-audio-content")
    second.write_bytes(b"same-audio-content")
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    service.import_lesson(request(audio=first, lesson_id="first-import"))

    with pytest.raises(DuplicateImportError) as error:
        service.import_lesson(request(audio=second, lesson_id="duplicate-import"))

    assert error.value.lesson_id == "first-import"
    assert service.repository.get_lesson("duplicate-import") is None
    assert not (workspace / "lessons" / "duplicate-import").exists()
    assert not (workspace / ".import-staging").exists()


def test_transaction_rechecks_duplicate_and_removes_final_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.write_bytes(b"same-audio-content")
    second.write_bytes(b"same-audio-content")
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    service.import_lesson(request(audio=first, lesson_id="first-import"))
    monkeypatch.setattr(service.repository, "find_asset_by_sha256", lambda *_args, **_kwargs: None)

    with pytest.raises(DuplicateImportError):
        service.import_lesson(request(audio=second, lesson_id="racing-import"))

    assert service.repository.get_lesson("racing-import") is None
    assert not (workspace / "lessons" / "racing-import").exists()


def test_cancelled_import_leaves_no_files_or_database_rows(tmp_path: Path) -> None:
    source = tmp_path / "large.wav"
    source.write_bytes(b"a" * (3 * 1024 * 1024))
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    token = ImportCancellationToken()

    def cancel_during_copy(_message: str, percent: int) -> None:
        if percent >= 20:
            token.cancel()

    result = service.import_lesson(
        request(audio=source, lesson_id="cancelled-import"),
        cancellation=token,
        progress=cancel_during_copy,
    )

    assert result.cancelled is True
    assert service.repository.get_lesson("cancelled-import") is None
    assert not (workspace / "lessons" / "cancelled-import").exists()
    assert not (workspace / ".import-staging").exists()


def test_database_failure_removes_committed_directory(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)

    def fail(*_args, **_kwargs) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service.repository, "import_lesson_bundle", fail)

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.import_lesson(request(lesson_id="failed-import"))

    assert not (workspace / "lessons" / "failed-import").exists()
    assert service.repository.get_lesson("failed-import") is None


def test_import_validation_rejects_unsupported_or_inconsistent_sources(
    tmp_path: Path,
) -> None:
    service = StudentContentService(tmp_path / "data")
    unsupported = tmp_path / "audio.exe"
    unsupported.write_bytes(b"data")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("text", encoding="utf-8")

    with pytest.raises(ImportValidationError, match="Неподдерживаемый формат"):
        service.import_lesson(request(audio=unsupported))
    with pytest.raises(ImportValidationError, match="одновременно"):
        service.import_lesson(request(audio=audio, transcript=transcript, enqueue=True))
    with pytest.raises(ImportValidationError, match="Укажите тему"):
        bad = request()
        service.import_lesson(
            LessonImportRequest(
                student=bad.student,
                subject=bad.subject,
                lesson_date=bad.lesson_date,
                topic=" ",
            )
        )
