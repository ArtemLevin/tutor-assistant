from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SegmentAction = Literal["keep", "trim", "drop"]
SegmentCategory = Literal[
    "educational",
    "greeting",
    "farewell",
    "audio_check",
    "video_check",
    "screen_sharing",
    "technical_issue",
    "small_talk",
    "filler",
    "duplicate",
    "background_noise",
    "other_non_educational",
]


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


class SegmentDecision(BaseModel):
    source_segment_id: int
    action: SegmentAction
    normalized_text: str | None = None
    category: SegmentCategory
    reason_code: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_action_text(self) -> SegmentDecision:
        if self.action == "drop" and self.normalized_text is not None:
            raise ValueError("action=drop must not contain normalized_text")
        if self.action == "trim" and not (self.normalized_text or "").strip():
            raise ValueError("action=trim requires non-empty normalized_text")
        return self


class NormalizationChunkRequest(BaseModel):
    schema_version: str = "1.0"
    lesson_id: str
    prompt_version: str
    mode: str
    segments: list[SourceSegment] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_segment_ids(self) -> NormalizationChunkRequest:
        ids = [segment.source_segment_id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("chunk contains duplicate source_segment_id values")
        return self


class NormalizationChunkResponse(BaseModel):
    decisions: list[SegmentDecision]


class NormalizedSegment(BaseModel):
    id: str
    source_segment_ids: list[int] = Field(min_length=1)
    speaker: str | None = None
    start: float | None = None
    end: float | None = None
    content_type: str
    text: str = Field(min_length=1)


class RemovedFragment(BaseModel):
    source_segment_ids: list[int] = Field(min_length=1)
    category: str
    reason_code: str
    text: str | None = None


class NormalizationStatistics(BaseModel):
    source_characters: int = Field(ge=0)
    normalized_characters: int = Field(ge=0)
    retained_ratio: float = Field(ge=0, le=1)
    source_segments: int = Field(ge=0)
    kept_segments: int = Field(ge=0)
    trimmed_segments: int = Field(ge=0)
    removed_segments: int = Field(ge=0)


class NormalizationQuality(BaseModel):
    schema_valid: bool
    all_source_segments_classified: bool
    numbers_preserved: bool
    formula_tokens_preserved: bool
    requires_manual_attention: bool
    warnings: list[str] = Field(default_factory=list)


class NormalizedTranscript(BaseModel):
    schema_version: str = "1.0"
    lesson_id: str
    source: dict
    normalizer: dict
    educational_text: str
    segments: list[NormalizedSegment]
    removed_fragments: list[RemovedFragment]
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


class NormalizationRun(BaseModel):
    id: int | None = None
    lesson_id: str
    source_sha256: str = Field(min_length=64, max_length=64)
    model: str
    prompt_version: str
    configuration_hash: str = Field(min_length=64, max_length=64)
    status: NormalizationRunStatus
    attempts: int = Field(default=0, ge=0)
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
    source_sha256: str
    configuration_hash: str
    started_at: datetime
    completed_at: datetime
    elapsed_seconds: float = Field(ge=0)
    chunk_count: int = Field(ge=0)
    attempts: int = Field(ge=0)
    status: NormalizationRunStatus


class NormalizationExecution(BaseModel):
    run: NormalizationRun | None
    transcript: NormalizedTranscript
    artifact_path: str
    manifest_path: str | None = None
    reused: bool = False


class OllamaDiagnostics(BaseModel):
    endpoint_local: bool
    reachable: bool
    version: str | None = None
    model_available: bool = False
    structured_output_valid: bool = False
    errors: list[str] = Field(default_factory=list)
