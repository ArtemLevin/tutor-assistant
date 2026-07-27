
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.content import StudentContentService
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.normalization.errors import (
    NormalizationResumeConfirmationRequired,
    OllamaTimeoutError,
)
from tutor_assistant.normalization.models import NormalizationRunStatus, SourceSegment
from tutor_assistant.normalization.protocol import CancellationToken, FakeNormalizationProvider
from tutor_assistant.normalization.service import NormalizationService


def _segments(count: int = 3) -> list[SourceSegment]:
    return [
        SourceSegment(
            source_segment_id=index,
            start=float(index),
            end=float(index + 1),
            speaker="П",
            text=f"Решаем уравнение x + {index} = {index + 2}.",
        )
        for index in range(1, count + 1)
    ]


def _setup(tmp_path: Path, provider, *, provider_name: str = "ollama"):
    content = StudentContentService(tmp_path / "data")
    lesson = Lesson(
        lesson_id="resume-lesson",
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 27),
        topic="Уравнения",
        status=JobStatus.REVIEW_REQUIRED,
    )
    content.create_lesson(lesson)
    config_data = {
        "provider": provider_name,
        "max_segments_per_chunk": 1,
        "max_input_characters": 1000,
        "context_overlap_segments": 0,
        "max_attempts": 2,
        "retry_backoff_seconds": 0,
    }
    if provider_name == "yandex_ai_studio":
        config_data.update(
            allow_cloud_processing=True,
            yandex_folder_id="folder",
        )
    config = NormalizationConfig(**config_data)
    service = NormalizationService(
        config,
        content,
        provider_factory=lambda _config, _model: provider,
    )
    return service, content, lesson


def test_failed_run_reuses_completed_chunks(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(
        responses=[
            lambda request: f"[П] {request.segments[0].text}",
            lambda request: f"[П] {request.segments[0].text}",
            OllamaTimeoutError("timeout"),
            OllamaTimeoutError("timeout"),
        ]
    )
    service, _content, lesson = _setup(tmp_path, provider)

    with pytest.raises(OllamaTimeoutError):
        service.normalize_lesson(lesson.lesson_id, source_segments=_segments())
    first_request_count = len(provider.requests)
    assert first_request_count == 4

    result = service.normalize_lesson(lesson.lesson_id, source_segments=_segments())

    assert len(provider.requests) == first_request_count + 1
    assert result.run and result.run.status == NormalizationRunStatus.REVIEW_REQUIRED
    assert result.transcript.statistics.reused_chunks == 2
    assert result.transcript.statistics.provider_requests == 1


def test_cancellation_preserves_completed_checkpoint(tmp_path: Path) -> None:
    token = CancellationToken()
    calls = 0

    def response(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            token.cancel()
        return f"[П] {request.segments[0].text}"

    provider = FakeNormalizationProvider(default=response)
    service, _content, lesson = _setup(tmp_path, provider)

    with pytest.raises(Exception, match="отменена"):
        service.normalize_lesson(
            lesson.lesson_id,
            source_segments=_segments(),
            cancellation=token,
        )

    result = service.normalize_lesson(
        lesson.lesson_id,
        source_segments=_segments(),
        cancellation=CancellationToken(),
    )
    assert result.transcript.statistics.reused_chunks == 1
    assert result.transcript.statistics.provider_requests == 2


def test_yandex_indeterminate_requires_explicit_confirmation(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider()
    service, _content, lesson = _setup(tmp_path, provider, provider_name="yandex_ai_studio")
    segments = _segments(1)

    source_hash = service._source_hash_for_test(segments)
    subject_profile = service._subject_profile_for_test(lesson.subject)
    config_hash = service._config_hash_for_test(
        service.config.effective_model,
        lesson.subject,
        subject_profile,
    )
    run, _created = service.runs.create_or_get(
        lesson_id=lesson.lesson_id,
        source_hash=source_hash,
        model=service.config.effective_model,
        prompt_version=subject_profile.prompt_version,
        config_hash=config_hash,
        provider=service.config.provider,
        force=False,
    )
    chunks = service._chunks_for_test(segments)
    service.checkpoints.prepare_chunks(
        run.id or 0,
        chunks,
        configuration_hash=config_hash,
        prompt_version=subject_profile.prompt_version,
        subject_profile=subject_profile.name.value,
    )
    service.checkpoints.mark_running(run.id or 0, 0)
    service.recover_interrupted()

    with pytest.raises(NormalizationResumeConfirmationRequired):
        service.normalize_lesson(lesson.lesson_id, source_segments=segments)
    assert provider.requests == []

    result = service.normalize_lesson(
        lesson.lesson_id,
        source_segments=segments,
        retry_indeterminate=True,
    )
    assert result.transcript.statistics.provider_requests == 1
