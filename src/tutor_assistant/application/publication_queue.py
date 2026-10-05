from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ..domain import Lesson
from ..publication_queue import (
    AutomaticPublicationJob,
    AutomaticPublicationQueue,
    AutomaticPublicationStatus,
    PublicationQueueStorage,
    StoredPublicationJobLike,
)
from ..publisher import (
    GitError,
    PublicationBlockedError,
    PublicationConflictError,
)


AUTOMATIC_PUBLICATION_BACKOFF_SECONDS = (30, 120, 600, 1800)


class PublicationFailureDisposition(StrEnum):
    RETRY_REQUIRED = "retry_required"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class PublicationFailureDecision:
    disposition: PublicationFailureDisposition
    retry_after_seconds: int | None = None


def classify_publication_failure(
    error: BaseException,
    *,
    attempts: int,
) -> PublicationFailureDecision:
    if isinstance(error, PublicationConflictError):
        return PublicationFailureDecision(PublicationFailureDisposition.CONFLICT)
    if isinstance(error, (PublicationBlockedError, RuntimeError)):
        return PublicationFailureDecision(PublicationFailureDisposition.BLOCKED)
    if isinstance(error, GitError):
        retry_index = max(attempts - 1, 0)
        if retry_index < len(AUTOMATIC_PUBLICATION_BACKOFF_SECONDS):
            return PublicationFailureDecision(
                PublicationFailureDisposition.RETRY_REQUIRED,
                AUTOMATIC_PUBLICATION_BACKOFF_SECONDS[retry_index],
            )
        return PublicationFailureDecision(PublicationFailureDisposition.BLOCKED)
    return PublicationFailureDecision(PublicationFailureDisposition.BLOCKED)


@dataclass(frozen=True, slots=True)
class PublicationPumpContext:
    shutdown_requested: bool = False


@dataclass(frozen=True, slots=True)
class PublicationSubmission:
    job_id: str
    lesson: Lesson
    revision_number: int
    content_sha256: str
    repository_path: str
    attempts: int


@dataclass(frozen=True, slots=True)
class PublicationQueueEntrySnapshot:
    job_id: str
    student_name: str
    topic: str
    status: str
    attempts: int
    error: str | None
    next_attempt_at: datetime | None


@dataclass(frozen=True, slots=True)
class PublicationQueueSnapshot:
    entries: tuple[PublicationQueueEntrySnapshot, ...]

    @property
    def unfinished_count(self) -> int:
        return sum(
            entry.status
            in {
                AutomaticPublicationStatus.WAITING.value,
                AutomaticPublicationStatus.RUNNING.value,
                AutomaticPublicationStatus.RETRY_REQUIRED.value,
            }
            for entry in self.entries
        )


class PublicationQueueCoordinator:
    """Qt-free coordinator for the durable automatic publication queue."""

    def __init__(self, storage: PublicationQueueStorage | None = None) -> None:
        self._queue = AutomaticPublicationQueue(storage)

    @property
    def active(self) -> AutomaticPublicationJob | None:
        return self._queue.active

    def enqueue(
        self,
        lesson: Lesson,
        *,
        revision_number: int,
        content_sha256: str,
        repository_path: str,
    ) -> AutomaticPublicationJob:
        return self._queue.enqueue(
            lesson,
            revision_number=revision_number,
            content_sha256=content_sha256,
            repository_path=repository_path,
        )

    def pump(
        self,
        context: PublicationPumpContext,
        *,
        now: datetime | None = None,
    ) -> PublicationSubmission | None:
        if context.shutdown_requested:
            return None
        job = self._queue.start_next(now=now)
        if job is None:
            return None
        return PublicationSubmission(
            job_id=job.id,
            lesson=job.lesson,
            revision_number=job.revision_number,
            content_sha256=job.content_sha256,
            repository_path=job.repository_path,
            attempts=job.attempts,
        )

    def published(self, job_id: str) -> AutomaticPublicationJob:
        return self._queue.publish(job_id)

    def retry_required(
        self,
        job_id: str,
        error: str,
        *,
        next_attempt_at: datetime,
    ) -> AutomaticPublicationJob:
        return self._queue.retry_required(
            job_id,
            error,
            next_attempt_at=next_attempt_at,
        )

    def conflict(self, job_id: str, error: str) -> AutomaticPublicationJob:
        return self._queue.conflict(job_id, error)

    def blocked(self, job_id: str, error: str) -> AutomaticPublicationJob:
        return self._queue.block(job_id, error)

    def retry(self, job_id: str) -> AutomaticPublicationJob:
        return self._queue.retry(job_id)

    def get(self, job_id: str) -> AutomaticPublicationJob | None:
        return self._queue.get(job_id)

    def snapshot(self) -> PublicationQueueSnapshot:
        return PublicationQueueSnapshot(
            entries=tuple(
                PublicationQueueEntrySnapshot(
                    job_id=job.id,
                    student_name=job.lesson.student.full_name,
                    topic=job.lesson.topic,
                    status=job.status.value,
                    attempts=job.attempts,
                    error=job.error,
                    next_attempt_at=job.next_attempt_at,
                )
                for job in self._queue.jobs
            )
        )

    def restore_history(
        self,
        lessons: Sequence[Lesson],
        stored_jobs: Iterable[StoredPublicationJobLike],
    ) -> int:
        lessons_by_id = {lesson.lesson_id: lesson for lesson in lessons}
        restored = 0
        for stored in stored_jobs:
            lesson = lessons_by_id.get(stored.lesson_id)
            if lesson is None:
                continue
            self._queue.restore(lesson, stored)
            restored += 1
        return restored
