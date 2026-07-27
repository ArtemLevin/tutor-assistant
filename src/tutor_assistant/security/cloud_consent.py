from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..content.repository import StudentContentRepository
    from ..normalization.models import NormalizationChunkRequest


def _now() -> datetime:
    return datetime.now(UTC)


class CloudConsentScope(StrEnum):
    ONCE = "once"
    SESSION = "session"


class CloudConsentDecision(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"


class CloudSegmentEnvelope(BaseModel):
    source_segment_id: int
    speaker: str | None = None
    text: str
    context_only: bool = False


class CloudRequestEnvelope(BaseModel):
    prompt_version: str
    mode: str
    lesson_subject: str
    subject_profile: str
    segments: list[CloudSegmentEnvelope] = Field(min_length=1)

    @classmethod
    def from_normalization_request(
        cls,
        request: NormalizationChunkRequest,
    ) -> CloudRequestEnvelope:
        return cls(
            prompt_version=request.prompt_version,
            mode=request.mode,
            lesson_subject=request.lesson_subject,
            subject_profile=request.subject_profile,
            segments=[
                CloudSegmentEnvelope(
                    source_segment_id=item.source_segment_id,
                    speaker=item.speaker,
                    text=item.text,
                    context_only=item.context_only,
                )
                for item in request.segments
            ],
        )

    def as_normalization_request(self):
        from ..normalization.models import NormalizationChunkRequest, SourceSegment

        return NormalizationChunkRequest(
            lesson_id="cloud-redacted",
            prompt_version=self.prompt_version,
            mode=self.mode,
            lesson_subject=self.lesson_subject,
            subject_profile=self.subject_profile,
            segments=[
                SourceSegment(
                    source_segment_id=item.source_segment_id,
                    speaker=item.speaker,
                    text=item.text,
                    context_only=item.context_only,
                )
                for item in self.segments
            ],
        )


class CloudProcessingRequest(BaseModel):
    provider: Literal["yandex_ai_studio"] = "yandex_ai_studio"
    model: str
    purpose: str = "transcript_filter"
    source_sha256: str = Field(min_length=64, max_length=64)
    configuration_hash: str = Field(min_length=64, max_length=64)
    prompt_version: str
    subject_profile: str
    segment_count: int = Field(ge=0)
    character_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    created_at: datetime = Field(default_factory=_now)

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"created_at"})
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class CloudConsentReceipt(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    request_fingerprint: str = Field(min_length=64, max_length=64)
    scope: CloudConsentScope
    decision: CloudConsentDecision = CloudConsentDecision.ALLOWED
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime | None = None

    @classmethod
    def grant(
        cls,
        request: CloudProcessingRequest,
        scope: CloudConsentScope = CloudConsentScope.ONCE,
    ) -> CloudConsentReceipt:
        expires_at = _now() + timedelta(hours=12) if scope == CloudConsentScope.SESSION else None
        return cls(
            request_fingerprint=request.fingerprint,
            scope=scope,
            expires_at=expires_at,
        )

    def is_valid_for(self, request: CloudProcessingRequest) -> bool:
        if self.decision != CloudConsentDecision.ALLOWED:
            return False
        if self.request_fingerprint != request.fingerprint:
            return False
        return self.expires_at is None or self.expires_at > _now()


class CloudConsentSession:
    def __init__(self) -> None:
        self._receipts: dict[str, CloudConsentReceipt] = {}

    def grant(
        self,
        request: CloudProcessingRequest,
        scope: CloudConsentScope,
    ) -> CloudConsentReceipt:
        receipt = CloudConsentReceipt.grant(request, scope)
        if scope == CloudConsentScope.SESSION:
            self._receipts[request.fingerprint] = receipt
        return receipt

    def find(self, request: CloudProcessingRequest) -> CloudConsentReceipt | None:
        receipt = self._receipts.get(request.fingerprint)
        if receipt and receipt.is_valid_for(request):
            return receipt
        self._receipts.pop(request.fingerprint, None)
        return None

    def clear(self) -> None:
        self._receipts.clear()


def validate_cloud_consent(
    request: CloudProcessingRequest,
    receipt: CloudConsentReceipt | None,
    *,
    policy: str,
) -> None:
    if policy == "disabled":
        raise PermissionError("Облачная обработка отключена политикой конфигурации")
    if receipt is None:
        raise PermissionError("Для передачи транскрипта в облако требуется явное согласие")
    if not receipt.is_valid_for(request):
        raise PermissionError("Согласие не соответствует текущему источнику или конфигурации")


class CloudAuditStore:
    def __init__(self, repository: StudentContentRepository) -> None:
        self.repository = repository

    def record_consent(
        self,
        receipt: CloudConsentReceipt,
        request: CloudProcessingRequest,
        *,
        lesson_id: str,
        run_id: int | None,
    ) -> str:
        now = _now().isoformat()
        with self.repository.connect() as db:
            existing = db.execute(
                "SELECT lesson_id, run_id, request_fingerprint FROM cloud_processing_consents WHERE id=?",
                (receipt.id,),
            ).fetchone()
            if existing:
                if (
                    existing["lesson_id"] != lesson_id
                    or existing["request_fingerprint"] != request.fingerprint
                    or (
                        existing["run_id"] is not None
                        and run_id is not None
                        and int(existing["run_id"]) != run_id
                    )
                ):
                    raise PermissionError("Consent receipt уже использован для другого запуска")
                if existing["run_id"] is None and run_id is not None:
                    db.execute(
                        "UPDATE cloud_processing_consents SET run_id=? WHERE id=?",
                        (run_id, receipt.id),
                    )
                    db.commit()
                return receipt.id
            db.execute(
                """
                INSERT INTO cloud_processing_consents (
                    id, lesson_id, run_id, provider, model, purpose,
                    source_sha256, configuration_hash, prompt_version,
                    subject_profile, segment_count, character_count, chunk_count,
                    request_fingerprint, scope, decision, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.id,
                    lesson_id,
                    run_id,
                    request.provider,
                    request.model,
                    request.purpose,
                    request.source_sha256,
                    request.configuration_hash,
                    request.prompt_version,
                    request.subject_profile,
                    request.segment_count,
                    request.character_count,
                    request.chunk_count,
                    request.fingerprint,
                    receipt.scope.value,
                    receipt.decision.value,
                    now,
                    receipt.expires_at.isoformat() if receipt.expires_at else None,
                ),
            )
            db.commit()
        return receipt.id

    def request_started(
        self,
        *,
        consent_id: str,
        run_id: int | None,
        chunk_index: int,
        provider: str,
        model: str,
        request_fingerprint: str,
    ) -> str:
        event_id = uuid.uuid4().hex
        with self.repository.connect() as db:
            db.execute(
                """
                INSERT INTO cloud_request_events (
                    id, consent_id, run_id, chunk_index, provider, model,
                    event, request_fingerprint, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'request_started', ?, ?)
                """,
                (
                    event_id,
                    consent_id,
                    run_id,
                    chunk_index,
                    provider,
                    model,
                    request_fingerprint,
                    _now().isoformat(),
                ),
            )
            db.commit()
        return event_id

    def finish_request(
        self,
        event_id: str,
        *,
        event: str,
        response_sha256: str | None = None,
        error_code: str | None = None,
    ) -> None:
        if event not in {"request_completed", "request_failed", "request_indeterminate"}:
            raise ValueError(f"Неизвестное cloud request event: {event}")
        with self.repository.connect() as db:
            db.execute(
                """
                UPDATE cloud_request_events
                SET event=?, response_sha256=?, error_code=?, completed_at=?
                WHERE id=?
                """,
                (
                    event,
                    response_sha256,
                    error_code,
                    _now().isoformat(),
                    event_id,
                ),
            )
            db.commit()

    def record_retry_confirmation(
        self,
        *,
        consent_id: str,
        run_id: int | None,
        chunk_index: int,
        provider: str,
        model: str,
    ) -> None:
        timestamp = _now().isoformat()
        with self.repository.connect() as db:
            db.execute(
                """
                INSERT INTO cloud_request_events (
                    id, consent_id, run_id, chunk_index, provider, model,
                    event, request_fingerprint, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'retry_confirmed', ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    consent_id,
                    run_id,
                    chunk_index,
                    provider,
                    model,
                    hashlib.sha256(
                        f"{consent_id}:{run_id}:{chunk_index}:retry".encode("utf-8")
                    ).hexdigest(),
                    timestamp,
                    timestamp,
                ),
            )
            db.commit()
