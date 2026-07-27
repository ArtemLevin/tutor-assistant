from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.content import StudentContentService
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.normalization.errors import CloudProcessingConsentRequiredError
from tutor_assistant.normalization.protocol import FakeNormalizationProvider
from tutor_assistant.normalization.service import NormalizationService
from tutor_assistant.security.cloud_consent import CloudConsentReceipt


def _setup(tmp_path: Path):
    workspace = tmp_path / "data"
    content = StudentContentService(workspace)
    lesson = Lesson(
        lesson_id="cloud-consent-lesson",
        student=Student(id="student", full_name="Приватное имя"),
        subject="mathematics",
        lesson_date=date(2026, 7, 27),
        topic="Уравнения",
        status=JobStatus.REVIEW_REQUIRED,
    )
    lesson = content.create_lesson(lesson)
    transcript_dir = workspace / "lessons" / lesson.lesson_id / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    source = transcript_dir / "00_raw_segments.json"
    source.write_text(
        json.dumps(
            [
                {
                    "source_segment_id": 1,
                    "speaker": "П",
                    "text": "Решаем уравнение x + 2 = 5.",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    lesson.artifacts.segments_json = str(source.resolve())
    content.persist_pipeline_lesson(lesson, frozenset({"artifacts"}))
    provider = FakeNormalizationProvider(default=lambda _request: "[П] Решаем уравнение x + 2 = 5.")
    config = NormalizationConfig(
        provider="yandex_ai_studio",
        allow_cloud_processing=True,
        yandex_folder_id="folder",
        retry_backoff_seconds=0,
    )
    service = NormalizationService(
        config,
        content,
        provider_factory=lambda _config, _model: provider,
    )
    return service, content, lesson, provider


def test_cloud_service_blocks_before_provider_without_receipt(tmp_path: Path) -> None:
    service, _content, lesson, provider = _setup(tmp_path)

    with pytest.raises(CloudProcessingConsentRequiredError):
        service.normalize_lesson(lesson.lesson_id)

    assert provider.requests == []


def test_cloud_service_records_consent_and_request_metadata(tmp_path: Path) -> None:
    service, content, lesson, provider = _setup(tmp_path)
    request = service.cloud_processing_request(lesson.lesson_id)
    receipt = CloudConsentReceipt.grant(request)

    result = service.normalize_lesson(
        lesson.lesson_id,
        cloud_consent=receipt,
    )

    assert result.run is not None
    assert len(provider.requests) == 1
    with content.repository.connect() as db:
        consent = db.execute("SELECT * FROM cloud_processing_consents").fetchone()
        event = db.execute("SELECT * FROM cloud_request_events").fetchone()
    assert consent["lesson_id"] == lesson.lesson_id
    assert consent["request_fingerprint"] == request.fingerprint
    assert event["event"] == "request_completed"
    assert "Приватное имя" not in repr(dict(consent))
