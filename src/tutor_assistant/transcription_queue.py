from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .domain import Lesson


class QueueStorage(Protocol):
    def save_transcription_job(
        self,
        lesson_id: str,
        audio_path: str,
        status: str,
        error: str | None = None,
        *,
        increment_attempts: bool = False,
    ) -> None: ...


class QueueStatus(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


@dataclass
class TranscriptionJob:
    lesson: Lesson
    audio: Path
    status: QueueStatus = QueueStatus.WAITING
    error: str | None = None

    @property
    def id(self) -> str:
        return self.lesson.lesson_id


class TranscriptionQueue:
    """A deterministic single-worker queue; thread execution is owned by the UI layer."""

    def __init__(self, storage: QueueStorage | None = None) -> None:
        self._waiting: deque[str] = deque()
        self._jobs: dict[str, TranscriptionJob] = {}
        self._active_id: str | None = None
        self._storage = storage

    def _persist(self, job: TranscriptionJob, *, increment_attempts: bool = False) -> None:
        if self._storage:
            self._storage.save_transcription_job(
                job.id,
                str(job.audio.resolve()),
                job.status.value,
                job.error,
                increment_attempts=increment_attempts,
            )

    @property
    def jobs(self) -> tuple[TranscriptionJob, ...]:
        return tuple(self._jobs.values())

    @property
    def active(self) -> TranscriptionJob | None:
        return self._jobs.get(self._active_id) if self._active_id else None

    @property
    def unfinished_count(self) -> int:
        return sum(job.status in {QueueStatus.WAITING, QueueStatus.RUNNING} for job in self.jobs)

    def enqueue(self, lesson: Lesson, audio: Path) -> TranscriptionJob:
        existing = self._jobs.get(lesson.lesson_id)
        if existing and existing.status in {QueueStatus.WAITING, QueueStatus.RUNNING}:
            return existing
        job = TranscriptionJob(lesson=lesson, audio=audio)
        self._jobs[job.id] = job
        self._waiting.append(job.id)
        self._persist(job)
        return job

    def restore(
        self,
        lesson: Lesson,
        audio: Path,
        status: QueueStatus,
        error: str | None = None,
    ) -> TranscriptionJob:
        restored_status = QueueStatus.WAITING if status == QueueStatus.RUNNING else status
        job = TranscriptionJob(lesson=lesson, audio=audio, status=restored_status, error=error)
        self._jobs[job.id] = job
        if restored_status == QueueStatus.WAITING:
            self._waiting.append(job.id)
        if restored_status != status:
            self._persist(job)
        return job

    def start_next(self) -> TranscriptionJob | None:
        if self._active_id:
            return None
        while self._waiting:
            job_id = self._waiting.popleft()
            job = self._jobs[job_id]
            if job.status != QueueStatus.WAITING:
                continue
            job.status = QueueStatus.RUNNING
            self._active_id = job_id
            self._persist(job, increment_attempts=True)
            return job
        return None

    def complete(self, job_id: str, lesson: Lesson) -> TranscriptionJob:
        job = self._jobs[job_id]
        job.lesson = lesson
        job.status = QueueStatus.READY
        job.error = None
        if self._active_id == job_id:
            self._active_id = None
        self._persist(job)
        return job

    def fail(self, job_id: str, error: str) -> TranscriptionJob:
        job = self._jobs[job_id]
        job.status = QueueStatus.FAILED
        job.error = error
        if self._active_id == job_id:
            self._active_id = None
        self._persist(job)
        return job

    def retry(self, job_id: str) -> TranscriptionJob:
        job = self._jobs[job_id]
        if job.status != QueueStatus.FAILED:
            raise ValueError("Повторный запуск доступен только для ошибочного задания")
        job.status = QueueStatus.WAITING
        job.error = None
        self._waiting.append(job_id)
        self._persist(job)
        return job

    def get(self, job_id: str) -> TranscriptionJob | None:
        return self._jobs.get(job_id)
