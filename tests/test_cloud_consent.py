from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tutor_assistant.content import StudentContentRepository
from tutor_assistant.domain import Lesson, Student
from tutor_assistant.security.cloud_consent import (
    CloudAuditStore,
    CloudConsentReceipt,
    CloudConsentScope,
    CloudConsentSession,
    CloudProcessingRequest,
    validate_cloud_consent,
)


def _request(**updates) -> CloudProcessingRequest:
    payload = {
        "model": "yandexgpt-lite",
        "source_sha256": "a" * 64,
        "configuration_hash": "b" * 64,
        "prompt_version": "filter-v3",
        "subject_profile": "mathematics",
        "segment_count": 3,
        "character_count": 120,
        "chunk_count": 1,
    }
    payload.update(updates)
    return CloudProcessingRequest(**payload)


def test_receipt_is_bound_to_exact_request_fingerprint() -> None:
    request = _request()
    receipt = CloudConsentReceipt.grant(request)

    validate_cloud_consent(request, receipt, policy="ask_every_time")
    with pytest.raises(PermissionError):
        validate_cloud_consent(
            _request(model="yandexgpt"),
            receipt,
            policy="ask_every_time",
        )


def test_session_reuses_only_exact_request() -> None:
    session = CloudConsentSession()
    request = _request()
    receipt = session.grant(request, CloudConsentScope.SESSION)

    assert session.find(request) == receipt
    assert session.find(_request(source_sha256="c" * 64)) is None


def test_disabled_policy_fails_closed() -> None:
    request = _request()
    with pytest.raises(PermissionError, match="отключена"):
        validate_cloud_consent(
            request,
            CloudConsentReceipt.grant(request),
            policy="disabled",
        )


def test_audit_store_persists_only_metadata(tmp_path: Path) -> None:
    repository = StudentContentRepository(tmp_path / "content.sqlite3")
    lesson = Lesson(
        lesson_id="lesson-cloud",
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 27),
        topic="Неравенства",
    )
    repository.upsert_lesson(lesson)
    request = _request()
    receipt = CloudConsentReceipt.grant(request)
    store = CloudAuditStore(repository)

    consent_id = store.record_consent(
        receipt,
        request,
        lesson_id=lesson.lesson_id,
        run_id=None,
    )
    event_id = store.request_started(
        consent_id=consent_id,
        run_id=None,
        chunk_index=0,
        provider="yandex_ai_studio",
        model=request.model,
        request_fingerprint="d" * 64,
    )
    store.finish_request(event_id, event="request_completed", response_sha256="e" * 64)

    with repository.connect() as db:
        consent = db.execute(
            "SELECT * FROM cloud_processing_consents WHERE id=?",
            (consent_id,),
        ).fetchone()
        event = db.execute(
            "SELECT * FROM cloud_request_events WHERE id=?",
            (event_id,),
        ).fetchone()
    assert consent["character_count"] == 120
    assert event["event"] == "request_completed"
    serialized = repr(dict(consent)) + repr(dict(event))
    assert "Ученик" not in serialized
    assert "secret" not in serialized
