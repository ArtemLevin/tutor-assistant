from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .domain import Lesson


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

    def __init__(self) -> None:
        self._waiting: deque[str] = deque()
        self._jobs: dict[str, TranscriptionJob] = {}
        self._active_id: str | None = None

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
            return job
        return None

    def complete(self, job_id: str, lesson: Lesson) -> TranscriptionJob:
        job = self._jobs[job_id]
        job.lesson = lesson
        job.status = QueueStatus.READY
        job.error = None
        if self._active_id == job_id:
            self._active_id = None
        return job

    def fail(self, job_id: str, error: str) -> TranscriptionJob:
        job = self._jobs[job_id]
        job.status = QueueStatus.FAILED
        job.error = error
        if self._active_id == job_id:
            self._active_id = None
        return job

    def get(self, job_id: str) -> TranscriptionJob | None:
        return self._jobs.get(job_id)
