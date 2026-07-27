from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pytest

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.content import StudentContentService
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.normalization.errors import (
    InvalidPlainTextOutputError,
    NormalizationCancelledError,
    OllamaTimeoutError,
    SourceTranscriptChangedError,
)
from tutor_assistant.normalization.models import (
    NormalizationRunStatus,
    SourceSegment,
)
from tutor_assistant.normalization.protocol import (
    CancellationToken,
    FakeNormalizationProvider,
)
from tutor_assistant.normalization.service import NormalizationService


def _setup(
    tmp_path: Path,
    provider: FakeNormalizationProvider,
    *,
    config: NormalizationConfig | None = None,
) -> tuple[NormalizationService, StudentContentService, Lesson, Path]:
    workspace = tmp_path / "data"
    content = StudentContentService(workspace)
    lesson = Lesson(
        lesson_id="normalization-lesson",
        student=Student(id="student", full_name="Обезличенный ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 27),
        topic="Метод интервалов",
        status=JobStatus.REVIEW_REQUIRED,
    )
    lesson = content.create_lesson(lesson)
    transcript_dir = workspace / "lessons" / lesson.lesson_id / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    source_path = transcript_dir / "00_raw_segments.json"
    source_payload = [
        {
            "start": 0.0,
            "end": 2.0,
            "speaker": "П",
            "text": "Здравствуйте, меня слышно?",
        },
        {
            "start": 2.0,
            "end": 8.0,
            "speaker": "П",
            "text": "Ну, сегодня решаем неравенство x + 2 > 5.",
        },
        {
            "start": 8.0,
            "end": 11.0,
            "speaker": "У",
            "text": "Я не понимаю, почему знак меняется.",
        },
    ]
    source_path.write_text(
        json.dumps(source_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    lesson.artifacts.segments_json = str(source_path.resolve())
    lesson.artifacts.verified_transcript = str((transcript_dir / "transcript_verified.txt").resolve())
    lesson = content.persist_pipeline_lesson(lesson, frozenset({"artifacts"}))
    service = NormalizationService(
        config or NormalizationConfig(retry_backoff_seconds=0),
        content,
        provider_factory=lambda _config, _model: provider,
    )
    return service, content, lesson, source_path


def _normalized_text(_request) -> str:
    return "[П] Сегодня решаем неравенство x + 2 > 5.\n[У] Я не понимаю, почему знак меняется."


def test_normalization_creates_separate_plain_text_artifact_and_run(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(default=_normalized_text)
    service, content, lesson, source_path = _setup(tmp_path, provider)
    source_before = source_path.read_bytes()

    result = service.normalize_lesson(lesson.lesson_id)

    assert result.run is not None
    assert result.run.status == NormalizationRunStatus.REVIEW_REQUIRED
    assert result.transcript.educational_text == _normalized_text(None)
    assert result.transcript.quality.plain_text_valid is True
    assert Path(result.artifact_path).name == "transcript_normalized.txt"
    assert Path(result.artifact_path).read_text(encoding="utf-8").strip() == _normalized_text(None)
    assert Path(result.manifest_path or "").is_file()
    assert source_path.read_bytes() == source_before
    stored = content.get_lesson(lesson.lesson_id).lesson
    assert stored.artifacts.normalized_transcript_text == result.artifact_path
    assert stored.artifacts.normalized_transcript_json is None
    assert stored.status == JobStatus.REVIEW_REQUIRED


def test_identical_run_is_reused_and_force_creates_new_run(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(default=_normalized_text)
    service, _content, lesson, _source_path = _setup(tmp_path, provider)

    first = service.normalize_lesson(lesson.lesson_id)
    reused = service.normalize_lesson(lesson.lesson_id)
    forced = service.normalize_lesson(lesson.lesson_id, force=True)

    assert reused.reused is True
    assert reused.transcript.educational_text == _normalized_text(None)
    assert reused.run and first.run and reused.run.id == first.run.id
    assert forced.run and first.run and forced.run.id != first.run.id


def test_invalid_first_plain_text_response_is_retried_once(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(
        responses=[
            '{"text":"wrong contract"}',
            _normalized_text,
        ]
    )
    service, _content, lesson, _source_path = _setup(tmp_path, provider)

    result = service.normalize_lesson(lesson.lesson_id)

    assert result.run is not None
    assert result.run.attempts == 2
    assert len(provider.requests) == 2


def test_failed_second_response_preserves_source_and_marks_run_failed(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(
        responses=[
            '{"text":"wrong contract"}',
            '{"text":"still wrong"}',
        ]
    )
    service, _content, lesson, source_path = _setup(tmp_path, provider)
    source_before = source_path.read_bytes()

    with pytest.raises(InvalidPlainTextOutputError):
        service.normalize_lesson(lesson.lesson_id)

    run = service.runs.latest(lesson.lesson_id)
    assert run and run.status == NormalizationRunStatus.FAILED
    assert source_path.read_bytes() == source_before


def test_apply_creates_revision_and_marks_run_approved(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(default=_normalized_text)
    service, content, lesson, _source_path = _setup(tmp_path, provider)
    result = service.normalize_lesson(lesson.lesson_id)

    run = service.apply_result(result.run.id if result.run else 0)

    assert run.status == NormalizationRunStatus.APPROVED
    revision = content.repository.current_transcript(lesson.lesson_id)
    assert revision is not None
    assert revision.created_by == "ollama:qwen3:8b"
    assert "x + 2 > 5" in revision.content
    assert content.get_lesson(lesson.lesson_id).lesson.status == JobStatus.READY


def test_yandex_apply_records_cloud_provider(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(default=_normalized_text)
    config = NormalizationConfig(
        provider="yandex_ai_studio",
        allow_cloud_processing=True,
        yandex_folder_id="folder",
        retry_backoff_seconds=0,
    )
    service, content, lesson, _source_path = _setup(tmp_path, provider, config=config)
    result = service.normalize_lesson(lesson.lesson_id)

    service.apply_result(result.run.id if result.run else 0)

    revision = content.repository.current_transcript(lesson.lesson_id)
    assert revision and revision.created_by == "yandex_ai_studio:yandexgpt-lite"


def test_apply_blocks_changed_source_sha(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(default=_normalized_text)
    service, _content, lesson, _source_path = _setup(tmp_path, provider)
    result = service.normalize_lesson(lesson.lesson_id)
    changed = [
        SourceSegment(
            source_segment_id=1,
            start=0,
            end=2,
            speaker="П",
            text="Исходный текст теперь другой.",
        )
    ]

    with pytest.raises(SourceTranscriptChangedError, match="Исходный транскрипт был изменён"):
        service.apply_result(
            result.run.id if result.run else 0,
            current_segments=changed,
        )

    run = service.runs.get(result.run.id if result.run else 0)
    assert run and run.status == NormalizationRunStatus.STALE


def test_dry_run_creates_only_temporary_text(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(default=_normalized_text)
    service, content, lesson, _source_path = _setup(tmp_path, provider)

    result = service.normalize_lesson(lesson.lesson_id, dry_run=True)

    assert result.run is None
    assert service.runs.latest(lesson.lesson_id) is None
    assert Path(result.artifact_path).suffix == ".txt"
    assert Path(result.artifact_path).is_file()
    stored = content.get_lesson(lesson.lesson_id).lesson
    assert stored.artifacts.normalized_transcript_text is None
    assert stored.status == JobStatus.REVIEW_REQUIRED


def test_cancellation_marks_run_cancelled_without_artifact(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(default=_normalized_text)
    service, _content, lesson, _source_path = _setup(tmp_path, provider)
    token = CancellationToken()
    token.cancel()

    with pytest.raises(NormalizationCancelledError):
        service.normalize_lesson(lesson.lesson_id, cancellation=token)

    run = service.runs.latest(lesson.lesson_id)
    assert run and run.status == NormalizationRunStatus.CANCELLED
    assert run.artifact_path is None


def test_timeout_is_retried_and_does_not_break_manual_lesson(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(
        responses=[
            OllamaTimeoutError("synthetic timeout"),
            OllamaTimeoutError("synthetic timeout"),
        ]
    )
    service, content, lesson, _source_path = _setup(tmp_path, provider)

    with pytest.raises(OllamaTimeoutError):
        service.normalize_lesson(lesson.lesson_id)

    run = service.runs.latest(lesson.lesson_id)
    assert run and run.status == NormalizationRunStatus.FAILED
    assert content.get_lesson(lesson.lesson_id).lesson.status == JobStatus.REVIEW_REQUIRED


def test_logs_do_not_contain_transcript_text(tmp_path: Path, caplog) -> None:
    provider = FakeNormalizationProvider(default=_normalized_text)
    service, _content, lesson, _source_path = _setup(tmp_path, provider)

    with caplog.at_level(logging.INFO):
        service.normalize_lesson(lesson.lesson_id)

    assert "Я не понимаю, почему знак меняется" not in caplog.text
    assert "event=content_filter_completed" in caplog.text
