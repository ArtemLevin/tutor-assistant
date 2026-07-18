from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path, PureWindowsPath

from pydantic import BaseModel, Field, field_validator

from ..domain import JobStatus, Lesson


def _validate_lesson_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError("lesson_id must be a filesystem-safe identifier")
    return value


def _validate_relative_path(value: str) -> str:
    value = value.strip().replace("\\", "/")
    path = Path(value)
    windows_path = PureWindowsPath(value)
    if (
        not value
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in path.parts
    ):
        raise ValueError("path must be relative to the content workspace")
    return path.as_posix().strip("/")


class AssetKind(StrEnum):
    AUDIO = "audio"
    METADATA = "metadata"
    TRANSCRIPT = "transcript"
    DOCUMENT = "document"
    OTHER = "other"


class LessonAsset(BaseModel):
    id: int | None = None
    lesson_id: str
    kind: AssetKind
    relative_path: str
    media_type: str = "application/octet-stream"
    size_bytes: int = Field(default=0, ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deleted_at: datetime | None = None

    @field_validator("lesson_id")
    @classmethod
    def validate_lesson_id(cls, value: str) -> str:
        return _validate_lesson_id(value)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        return value


class TranscriptRevision(BaseModel):
    id: int | None = None
    lesson_id: str
    revision_number: int = Field(ge=1)
    relative_path: str
    content: str
    content_sha256: str = Field(min_length=64, max_length=64)
    created_by: str = "teacher"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deleted_at: datetime | None = None

    @field_validator("lesson_id")
    @classmethod
    def validate_lesson_id(cls, value: str) -> str:
        return _validate_lesson_id(value)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("content_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("content_sha256 must be a 64-character hexadecimal digest")
        return value


class TranscriptDraft(BaseModel):
    lesson_id: str
    base_revision_number: int | None = Field(default=None, ge=1)
    content: str
    content_sha256: str = Field(min_length=64, max_length=64)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("lesson_id")
    @classmethod
    def validate_lesson_id(cls, value: str) -> str:
        return _validate_lesson_id(value)

    @field_validator("content_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("content_sha256 must be a 64-character hexadecimal digest")
        return value


class LessonFilters(BaseModel):
    student_id: str | None = None
    subject: str | None = None
    status: JobStatus | None = None
    query: str | None = None
    lesson_date_from: date | None = None
    lesson_date_to: date | None = None
    include_deleted: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

    @field_validator("student_id", "subject", "query")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class LessonPage(BaseModel):
    items: list[Lesson]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class LessonContent(BaseModel):
    lesson: Lesson
    assets: list[LessonAsset] = Field(default_factory=list)
    transcript: TranscriptRevision | None = None
    draft: TranscriptDraft | None = None
    deleted_at: datetime | None = None


class IndexReport(BaseModel):
    scanned_directories: int = 0
    indexed_lessons: int = 0
    indexed_assets: int = 0
    indexed_transcripts: int = 0
    skipped_directories: int = 0
    errors: list[str] = Field(default_factory=list)
