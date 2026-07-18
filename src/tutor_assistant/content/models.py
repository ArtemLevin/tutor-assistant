from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path, PureWindowsPath

from pydantic import BaseModel, Field, computed_field, field_validator

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


class TrashState(StrEnum):
    MOVING = "moving"
    TRASHED = "trashed"
    RESTORING = "restoring"
    PURGING = "purging"


class ContentOperationKind(StrEnum):
    DELETE = "delete"
    RESTORE = "restore"
    PURGE = "purge"


class ContentOperationStatus(StrEnum):
    PENDING = "pending"
    CLEANUP_PENDING = "cleanup_pending"
    COMPLETED = "completed"
    FAILED = "failed"


class IntegritySeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


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


class TrashEntry(BaseModel):
    lesson_id: str
    original_relative_path: str
    trash_relative_path: str
    staging_relative_path: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    state: TrashState
    deleted_at: datetime
    purge_after: datetime

    @field_validator("lesson_id")
    @classmethod
    def validate_lesson_id(cls, value: str) -> str:
        return _validate_lesson_id(value)

    @field_validator(
        "original_relative_path",
        "trash_relative_path",
        "staging_relative_path",
    )
    @classmethod
    def validate_paths(cls, value: str | None) -> str | None:
        return _validate_relative_path(value) if value is not None else None


class TrashItem(BaseModel):
    lesson: Lesson
    entry: TrashEntry


class TrashSummary(BaseModel):
    items: list[TrashItem] = Field(default_factory=list)
    total_size_bytes: int = Field(default=0, ge=0)
    expired_count: int = Field(default=0, ge=0)


class TrashActionResult(BaseModel):
    lesson_id: str
    size_bytes: int = Field(default=0, ge=0)
    operation: ContentOperationKind


class ContentOperation(BaseModel):
    id: str
    lesson_id: str
    operation: ContentOperationKind
    status: ContentOperationStatus
    source_relative_path: str | None = None
    destination_relative_path: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    details: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ContentIntegrityIssue(BaseModel):
    severity: IntegritySeverity
    code: str
    message: str
    lesson_id: str | None = None
    relative_path: str | None = None


class StorageUsage(BaseModel):
    lessons_bytes: int = Field(default=0, ge=0)
    trash_bytes: int = Field(default=0, ge=0)
    temporary_bytes: int = Field(default=0, ge=0)
    database_bytes: int = Field(default=0, ge=0)
    free_bytes: int = Field(default=0, ge=0)

    @computed_field
    @property
    def managed_bytes(self) -> int:
        return self.lessons_bytes + self.trash_bytes + self.temporary_bytes + self.database_bytes


class ContentIntegrityReport(BaseModel):
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    database_ok: bool = True
    database_message: str = "ok"
    fts_enabled: bool = False
    fts_documents: int = Field(default=0, ge=0)
    indexed_lessons: int = Field(default=0, ge=0)
    lesson_directories: int = Field(default=0, ge=0)
    trash_items: int = Field(default=0, ge=0)
    orphan_directories: list[str] = Field(default_factory=list)
    temporary_paths: list[str] = Field(default_factory=list)
    storage: StorageUsage = Field(default_factory=StorageUsage)
    issues: list[ContentIntegrityIssue] = Field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(issue.severity == IntegritySeverity.ERROR for issue in self.issues)

    @property
    def warnings(self) -> int:
        return sum(issue.severity == IntegritySeverity.WARNING for issue in self.issues)

    @property
    def healthy(self) -> bool:
        return self.database_ok and self.errors == 0


class TemporaryCleanupResult(BaseModel):
    removed_paths: list[str] = Field(default_factory=list)
    released_bytes: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)


class IndexReport(BaseModel):
    scanned_directories: int = 0
    indexed_lessons: int = 0
    indexed_assets: int = 0
    indexed_transcripts: int = 0
    skipped_directories: int = 0
    errors: list[str] = Field(default_factory=list)
