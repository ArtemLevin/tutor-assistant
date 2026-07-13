from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class JobStatus(StrEnum):
    DRAFT = "draft"
    RECORDING = "recording"
    RECORDED = "recorded"
    TRANSCRIBING = "transcribing"
    REVIEW_REQUIRED = "review_required"
    READY = "ready_for_generation"
    PUBLISHED = "published"
    GENERATED_TEX = "generated_tex"
    COMPILING_PDF = "compiling_pdf"
    PDF_REVIEW_REQUIRED = "pdf_review_required"
    COMPILE_FAILED = "compile_failed"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.DRAFT: {JobStatus.RECORDING, JobStatus.RECORDED, JobStatus.TRANSCRIBING, JobStatus.FAILED},
    JobStatus.RECORDING: {JobStatus.RECORDED, JobStatus.FAILED},
    JobStatus.RECORDED: {JobStatus.TRANSCRIBING, JobStatus.FAILED},
    JobStatus.TRANSCRIBING: {JobStatus.REVIEW_REQUIRED, JobStatus.FAILED},
    JobStatus.REVIEW_REQUIRED: {JobStatus.READY, JobStatus.TRANSCRIBING, JobStatus.FAILED},
    JobStatus.READY: {JobStatus.PUBLISHED, JobStatus.TRANSCRIBING, JobStatus.FAILED},
    JobStatus.PUBLISHED: {JobStatus.GENERATED_TEX, JobStatus.GENERATING, JobStatus.READY, JobStatus.FAILED},
    JobStatus.GENERATED_TEX: {JobStatus.COMPILING_PDF, JobStatus.READY, JobStatus.FAILED},
    JobStatus.COMPILING_PDF: {JobStatus.PDF_REVIEW_REQUIRED, JobStatus.COMPILE_FAILED, JobStatus.FAILED},
    JobStatus.PDF_REVIEW_REQUIRED: {JobStatus.GENERATING, JobStatus.COMPLETED, JobStatus.READY},
    JobStatus.COMPILE_FAILED: {JobStatus.GENERATED_TEX, JobStatus.COMPILING_PDF, JobStatus.FAILED},
    JobStatus.GENERATING: {JobStatus.COMPLETED, JobStatus.FAILED},
    JobStatus.COMPLETED: {JobStatus.READY},
    JobStatus.FAILED: {JobStatus.DRAFT, JobStatus.RECORDED, JobStatus.TRANSCRIBING, JobStatus.READY},
}


class InvalidStatusTransition(ValueError):
    pass


class Student(BaseModel):
    id: str
    full_name: str
    grade: int | None = None
    exam: str | None = None
    subjects: list[str] = Field(default_factory=list)
    repository_folder: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
            raise ValueError("student id must be a filesystem-safe slug")
        return value

    @field_validator("repository_folder")
    @classmethod
    def validate_repository_folder(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or not value.strip():
            raise ValueError("repository_folder must be a safe relative path")
        return path.as_posix().strip("/")

    @property
    def folder(self) -> str:
        return self.repository_folder or f"students/{self.id}"


class PipelineOptions(BaseModel):
    latex: bool = True
    compile_pdf: bool = True
    poster: bool = True
    web: bool = True
    update_student_index: bool = True


class ArtifactPaths(BaseModel):
    raw_transcript: str | None = None
    timestamped_transcript: str | None = None
    cleaned_transcript: str | None = None
    verified_transcript: str | None = None
    segments_json: str | None = None
    student_signals: str | None = None
    transcription_manifest: str | None = None
    teacher_transcript: str | None = None
    student_transcript: str | None = None


class PublicationInfo(BaseModel):
    branch: str
    repository_path: str
    commit: str
    pr_url: str | None = None
    warnings: list[str] = Field(default_factory=list)


class LatexState(BaseModel):
    attempt: int = 0
    tex_path: str | None = None
    pdf_path: str | None = None
    report_path: str | None = None
    preview_paths: list[str] = Field(default_factory=list)
    tex_blob_sha: str | None = None


class Lesson(BaseModel):
    schema_version: str = "1.0"
    lesson_id: str = Field(default_factory=lambda: uuid4().hex)
    student: Student
    subject: str
    lesson_date: date
    topic: str
    status: JobStatus = JobStatus.DRAFT
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_audio_local: str | None = None
    artifacts: ArtifactPaths = Field(default_factory=ArtifactPaths)
    pipeline: PipelineOptions = Field(default_factory=PipelineOptions)
    publication: PublicationInfo | None = None
    latex: LatexState = Field(default_factory=LatexState)
    error: str | None = None

    def transition(self, status: JobStatus, error: str | None = None, *, force: bool = False) -> None:
        if status != self.status and not force and status not in ALLOWED_TRANSITIONS[self.status]:
            raise InvalidStatusTransition(f"Недопустимый переход: {self.status.value} → {status.value}")
        self.status = status
        self.updated_at = datetime.now(UTC)
        self.error = error

    @property
    def date_slug(self) -> str:
        return self.lesson_date.isoformat()

    @property
    def lesson_slug(self) -> str:
        topic = re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9]+", "-", self.topic.lower()).strip("-")
        return f"{self.date_slug}_{topic[:60]}"

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def read_json(cls, path: Path) -> Lesson:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
