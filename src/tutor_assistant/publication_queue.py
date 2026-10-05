from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from .domain import Lesson


class AutomaticPublicationStatus(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    RETRY_REQUIRED = "retry_required"
    PUBLISHED = "published"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


class StoredPublicationJobLike(Protocol):
    lesson_id: str
    revision_number: int
    content_sha256: str
    repository_path: str
    status: str
    attempts: int
    error: str | None
    next_attempt_at: str | None


class PublicationQueueStorage(Protocol):
    def ensure_automatic_publication_job(
        self,
        lesson_id: str,
        revision_number: int,
        content_sha256: str,
        repository_path: str,
    ) -> StoredPublicationJobLike: ...

    def update_automatic_publication_job(
        self,
        lesson_id: str,
        status: str,
        *,
        error: str | None = None,
        next_attempt_at: str | None = None,
        increment_attempts: bool = False,
    ) -> StoredPublicationJobLike: ...


@dataclass
class AutomaticPublicationJob:
    lesson: Lesson
    revision_number: int
    content_sha256: str
    repository_path: str
    status: AutomaticPublicationStatus = AutomaticPublicationStatus.WAITING
    attempts: int = 0
    error: str | None = None
    next_attempt_at: datetime | None = None

    @property
    def id(self) -> str:
        return self.lesson.lesson_id


class AutomaticPublicationQueue:
    """Deterministic single-worker queue for durable automatic publication intents."""

    def __init__(self, storage: PublicationQueueStorage | None = None) -> None:
        self._jobs: dict[str, AutomaticPublicationJob] = {}
        self._waiting: deque[str] = deque()
        self._active_id: str | None = None
        self._storage = storage

    @staticmethod
    def _parse_next_attempt(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    @staticmethod
    def _same_payload(
        job: AutomaticPublicationJob,
        *,
        revision_number: int,
        content_sha256: str,
        repository_path: str,
    ) -> bool:
        return (
            job.revision_number == revision_number
            and job.content_sha256 == content_sha256
            and job.repository_path == repository_path
        )

    def _persist(
        self,
        job: AutomaticPublicationJob,
        *,
        increment_attempts: bool = False,
    ) -> None:
        if self._storage is None:
            return
        stored = self._storage.update_automatic_publication_job(
            job.id,
            job.status.value,
            error=job.error,
            next_attempt_at=(
                job.next_attempt_at.isoformat()
                if job.next_attempt_at is not None
                else None
            ),
            increment_attempts=increment_attempts,
        )
        job.attempts = stored.attempts

    def _schedule(self, job: AutomaticPublicationJob) -> None:
        if job.id not in self._waiting:
            self._waiting.append(job.id)

    @property
    def jobs(self) -> tuple[AutomaticPublicationJob, ...]:
        return tuple(self._jobs.values())

    @property
    def active(self) -> AutomaticPublicationJob | None:
        return self._jobs.get(self._active_id) if self._active_id else None

    def enqueue(
        self,
        lesson: Lesson,
        *,
        revision_number: int,
        content_sha256: str,
        repository_path: str,
    ) -> AutomaticPublicationJob:
        existing = self._jobs.get(lesson.lesson_id)
        if existing is not None:
            if not self._same_payload(
                existing,
                revision_number=revision_number,
                content_sha256=content_sha256,
                repository_path=repository_path,
            ):
                raise ValueError("Automatic publication job payload is immutable")
            return existing

        status = AutomaticPublicationStatus.WAITING
        attempts = 0
        error = None
        next_attempt_at = None
        if self._storage is not None:
            stored = self._storage.ensure_automatic_publication_job(
                lesson.lesson_id,
                revision_number,
                content_sha256,
                repository_path,
            )
            status = AutomaticPublicationStatus(stored.status)
            attempts = stored.attempts
            error = stored.error
            next_attempt_at = self._parse_next_attempt(stored.next_attempt_at)
            if status == AutomaticPublicationStatus.RUNNING:
                status = AutomaticPublicationStatus.RETRY_REQUIRED
                error = error or "Publication was interrupted before completion"

        job = AutomaticPublicationJob(
            lesson=lesson,
            revision_number=revision_number,
            content_sha256=content_sha256,
            repository_path=repository_path,
            status=status,
            attempts=attempts,
            error=error,
            next_attempt_at=next_attempt_at,
        )
        self._jobs[job.id] = job
        if status in {
            AutomaticPublicationStatus.WAITING,
            AutomaticPublicationStatus.RETRY_REQUIRED,
        }:
            self._schedule(job)
        if (
            self._storage is not None
            and status == AutomaticPublicationStatus.RETRY_REQUIRED
        ):
            self._persist(job)
        return job

    def restore(
        self,
        lesson: Lesson,
        stored: StoredPublicationJobLike,
    ) -> AutomaticPublicationJob:
        return self.enqueue(
            lesson,
            revision_number=stored.revision_number,
            content_sha256=stored.content_sha256,
            repository_path=stored.repository_path,
        )

    def start_next(
        self,
        *,
        now: datetime | None = None,
    ) -> AutomaticPublicationJob | None:
        if self._active_id is not None:
            return None
        current_time = now or datetime.now(UTC)
        candidates = len(self._waiting)
        for _ in range(candidates):
            job_id = self._waiting.popleft()
            job = self._jobs[job_id]
            if job.status not in {
                AutomaticPublicationStatus.WAITING,
                AutomaticPublicationStatus.RETRY_REQUIRED,
            }:
                continue
            if (
                job.status == AutomaticPublicationStatus.RETRY_REQUIRED
                and job.next_attempt_at is not None
                and job.next_attempt_at > current_time
            ):
                self._waiting.append(job_id)
                continue
            job.status = AutomaticPublicationStatus.RUNNING
            job.error = None
            job.next_attempt_at = None
            job.attempts += 1
            self._active_id = job_id
            self._persist(job, increment_attempts=True)
            return job
        return None

    def _finish(
        self,
        job_id: str,
        status: AutomaticPublicationStatus,
        *,
        error: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> AutomaticPublicationJob:
        job = self._jobs[job_id]
        if job.status != AutomaticPublicationStatus.RUNNING:
            raise ValueError("Publication job is not running")
        job.status = status
        job.error = error
        job.next_attempt_at = next_attempt_at
        if self._active_id == job_id:
            self._active_id = None
        if status == AutomaticPublicationStatus.RETRY_REQUIRED:
            self._schedule(job)
        self._persist(job)
        return job

    def publish(self, job_id: str) -> AutomaticPublicationJob:
        return self._finish(job_id, AutomaticPublicationStatus.PUBLISHED)

    def retry_required(
        self,
        job_id: str,
        error: str,
        *,
        next_attempt_at: datetime,
    ) -> AutomaticPublicationJob:
        return self._finish(
            job_id,
            AutomaticPublicationStatus.RETRY_REQUIRED,
            error=error,
            next_attempt_at=next_attempt_at,
        )

    def conflict(self, job_id: str, error: str) -> AutomaticPublicationJob:
        return self._finish(
            job_id,
            AutomaticPublicationStatus.CONFLICT,
            error=error,
        )

    def block(self, job_id: str, error: str) -> AutomaticPublicationJob:
        return self._finish(
            job_id,
            AutomaticPublicationStatus.BLOCKED,
            error=error,
        )

    def retry(self, job_id: str) -> AutomaticPublicationJob:
        job = self._jobs[job_id]
        if job.status not in {
            AutomaticPublicationStatus.RETRY_REQUIRED,
            AutomaticPublicationStatus.BLOCKED,
        }:
            raise ValueError("Publication job is not retryable")
        job.status = AutomaticPublicationStatus.WAITING
        job.error = None
        job.next_attempt_at = None
        self._schedule(job)
        self._persist(job)
        return job

    def get(self, job_id: str) -> AutomaticPublicationJob | None:
        return self._jobs.get(job_id)
