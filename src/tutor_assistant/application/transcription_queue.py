from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..domain import JobStatus, Lesson
from ..transcription_queue import (
    QueueStatus,
    QueueStorage,
    TranscriptionJob,
    TranscriptionQueue,
)


class StoredTranscriptionJobLike(Protocol):
    lesson_id: str
    audio_path: str
    status: str
    error: str | None


class TranscriptionAudioMissingError(FileNotFoundError):
    def __init__(self, path: Path) -> None:
        super().__init__(str(path))
        self.path = path


@dataclass(frozen=True, slots=True)
class TranscriptionPumpContext:
    shutdown_requested: bool = False
    normalization_active: bool = False


@dataclass(frozen=True, slots=True)
class TranscriptionSubmission:
    job_id: str
    lesson: Lesson
    audio: Path


@dataclass(frozen=True, slots=True)
class TranscriptionQueueEntrySnapshot:
    job_id: str
    student_name: str
    topic: str
    status: str
    error: str | None


@dataclass(frozen=True, slots=True)
class TranscriptionQueueSnapshot:
    entries: tuple[TranscriptionQueueEntrySnapshot, ...]
    unfinished_count: int
    ready_count: int

    @property
    def visible_count(self) -> int:
        return self.unfinished_count + self.ready_count


RetryStateWriter = Callable[[Lesson], None]


class TranscriptionQueueCoordinator:
    """Qt-free orchestration around the persistent single-worker transcription queue."""

    def __init__(
        self,
        storage: QueueStorage | None = None,
        *,
        retry_state_writer: RetryStateWriter | None = None,
    ) -> None:
        self._queue = TranscriptionQueue(storage)
        self._retry_state_writer = retry_state_writer

    @property
    def active(self) -> TranscriptionJob | None:
        return self._queue.active

    def snapshot(self) -> TranscriptionQueueSnapshot:
        jobs = self._queue.jobs
        return TranscriptionQueueSnapshot(
            entries=tuple(
                TranscriptionQueueEntrySnapshot(
                    job_id=job.id,
                    student_name=job.lesson.student.full_name,
                    topic=job.lesson.topic,
                    status=job.status.value,
                    error=job.error,
                )
                for job in jobs
            ),
            unfinished_count=self._queue.unfinished_count,
            ready_count=sum(job.status == QueueStatus.READY for job in jobs),
        )

    def enqueue(self, lesson: Lesson, audio: Path) -> TranscriptionJob:
        return self._queue.enqueue(lesson, audio)

    def pump(self, context: TranscriptionPumpContext) -> TranscriptionSubmission | None:
        if context.shutdown_requested or context.normalization_active:
            return None
        job = self._queue.start_next()
        if job is None:
            return None
        return TranscriptionSubmission(job_id=job.id, lesson=job.lesson, audio=job.audio)

    def complete(self, job_id: str, lesson: Lesson) -> TranscriptionJob:
        return self._queue.complete(job_id, lesson)

    def fail(self, job_id: str, error: str) -> TranscriptionJob:
        return self._queue.fail(job_id, error)

    def get(self, job_id: str) -> TranscriptionJob | None:
        return self._queue.get(job_id)

    def retry(self, job_id: str) -> TranscriptionJob:
        job = self._queue.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status != QueueStatus.FAILED:
            raise ValueError("Повторный запуск доступен только для ошибочного задания")
        if not job.audio.is_file():
            raise TranscriptionAudioMissingError(job.audio)
        job.lesson.transition(JobStatus.RECORDED, force=True)
        if self._retry_state_writer is not None:
            self._retry_state_writer(job.lesson)
        return self._queue.retry(job_id)

    def discard(self, job_id: str) -> None:
        self._queue.discard(job_id)

    def restore_history(
        self,
        lessons: Sequence[Lesson],
        stored_jobs: Iterable[StoredTranscriptionJobLike],
    ) -> int:
        restored = 0
        lessons_by_id = {lesson.lesson_id: lesson for lesson in lessons}
        for stored in stored_jobs:
            lesson = lessons_by_id.get(stored.lesson_id)
            if lesson is None:
                continue
            try:
                status = QueueStatus(stored.status)
            except ValueError:
                continue
            audio = Path(stored.audio_path)
            if status in {QueueStatus.WAITING, QueueStatus.RUNNING} and not audio.is_file():
                continue
            self._queue.restore(lesson, audio, status, stored.error)
            restored += 1

        known = {job.id for job in self._queue.jobs}
        for lesson in reversed(tuple(lessons)):
            if lesson.status not in {JobStatus.RECORDED, JobStatus.TRANSCRIBING}:
                continue
            if lesson.lesson_id in known or not lesson.source_audio_local:
                continue
            audio = Path(lesson.source_audio_local)
            if not audio.is_file():
                continue
            self._queue.enqueue(lesson, audio)
            known.add(lesson.lesson_id)
            restored += 1
        return restored
