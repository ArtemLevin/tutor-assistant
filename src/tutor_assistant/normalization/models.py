from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class SourceSegment(BaseModel):
    source_segment_id: int
    start: float | None = None
    end: float | None = None
    speaker: str | None = None
    text: str
    context_only: bool = False

    @model_validator(mode="after")
    def validate_time_range(self) -> SourceSegment:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("segment end must not be earlier than start")
        return self


class NormalizationChunkRequest(BaseModel):
    lesson_id: str
    prompt_version: str
    mode: str
    segments: list[SourceSegment] = Field(min_length=1)
    lesson_subject: str = "generic"
    subject_profile: str = "generic"

    @model_validator(mode="after")
    def validate_segment_ids(self) -> NormalizationChunkRequest:
        ids = [segment.source_segment_id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("chunk contains duplicate source_segment_id values")
        return self


class NormalizationStatistics(BaseModel):
    source_characters: int = Field(ge=0)
    normalized_characters: int = Field(ge=0)
    retained_ratio: float = Field(ge=0, le=1)
    source_segments: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    completed_chunks: int = Field(default=0, ge=0)
    reused_chunks: int = Field(default=0, ge=0)
    provider_requests: int = Field(default=0, ge=0)


class NormalizationQuality(BaseModel):
    plain_text_valid: bool
    numbers_preserved: bool
    formula_tokens_preserved: bool
    protected_content_preserved: bool
    requires_manual_attention: bool
    warnings: list[str] = Field(default_factory=list)
    subject_units_preserved: bool = True


class NormalizedTranscript(BaseModel):
    """In-memory review model; the persisted transcript artifact is plain UTF-8 text."""

    lesson_id: str
    source: dict
    normalizer: dict
    educational_text: str
    statistics: NormalizationStatistics
    quality: NormalizationQuality


class NormalizationRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    FAILED = "failed"
    STALE = "stale"
    CANCELLED = "cancelled"


class NormalizationChunkStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class NormalizationChunkCheckpoint(BaseModel):
    run_id: int
    chunk_index: int = Field(ge=0)
    chunk_sha256: str = Field(min_length=64, max_length=64)
    target_ids: tuple[int, ...]
    status: NormalizationChunkStatus
    attempts: int = Field(default=0, ge=0)
    normalized_text: str | None = None
    quality: NormalizationQuality | None = None
    response_sha256: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class NormalizationProgress(BaseModel):
    run_id: int | None = None
    current_chunk: int | None = None
    total_chunks: int = Field(ge=0)
    completed_chunks: int = Field(ge=0)
    reused_chunks: int = Field(ge=0)
    provider_requests: int = Field(ge=0)
    current_attempt: int | None = None
    state: str


class NormalizationRun(BaseModel):
    id: int | None = None
    lesson_id: str
    source_sha256: str = Field(min_length=64, max_length=64)
    model: str
    prompt_version: str
    configuration_hash: str = Field(min_length=64, max_length=64)
    status: NormalizationRunStatus
    attempts: int = Field(default=0, ge=0)
    provider: str = "ollama"
    resume_count: int = Field(default=0, ge=0)
    last_resumed_at: datetime | None = None
    artifact_path: str | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    approved_at: datetime | None = None


class NormalizationManifest(BaseModel):
    provider: str
    model: str
    prompt_version: str
    source_artifact: str
    source_sha256: str
    configuration_hash: str
    started_at: datetime
    completed_at: datetime
    elapsed_seconds: float = Field(ge=0)
    chunk_count: int = Field(ge=0)
    attempts: int = Field(ge=0)
    status: NormalizationRunStatus
    statistics: NormalizationStatistics
    quality: NormalizationQuality
    lesson_subject: str = "generic"
    subject_profile: str = "generic"
    checkpoint_schema_version: int = 1
    completed_chunks: int = 0
    reused_chunks: int = 0
    provider_requests: int = 0
    resume_count: int = 0


class NormalizationExecution(BaseModel):
    run: NormalizationRun | None
    transcript: NormalizedTranscript
    artifact_path: str
    manifest_path: str | None = None
    reused: bool = False


class NormalizationDiagnostics(BaseModel):
    provider: str
    endpoint: str
    endpoint_local: bool
    reachable: bool
    version: str | None = None
    model_available: bool = False
    plain_text_valid: bool = False
    errors: list[str] = Field(default_factory=list)


OllamaDiagnostics = NormalizationDiagnostics
