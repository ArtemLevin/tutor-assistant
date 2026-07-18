from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from threading import Event

from ..domain import Lesson, Student
from .models import TranscriptRevision


class ImportValidationError(ValueError):
    pass


class ImportCancelledError(RuntimeError):
    pass


class DuplicateImportError(ValueError):
    def __init__(self, sha256: str, lesson_id: str) -> None:
        self.sha256 = sha256
        self.lesson_id = lesson_id
        super().__init__(f"Этот аудиофайл уже импортирован в занятие {lesson_id} (SHA-256: {sha256[:12]}…)")


class ImportCancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise ImportCancelledError("Импорт отменён")


@dataclass(frozen=True)
class LessonImportRequest:
    student: Student
    subject: str
    lesson_date: date
    topic: str
    audio_source: Path | None = None
    transcript_source: Path | None = None
    enqueue_audio: bool = False
    lesson_id: str | None = None


@dataclass(frozen=True)
class LessonImportResult:
    lesson: Lesson | None = None
    audio_path: Path | None = None
    transcript: TranscriptRevision | None = None
    audio_sha256: str | None = None
    enqueue_audio: bool = False
    cancelled: bool = False
