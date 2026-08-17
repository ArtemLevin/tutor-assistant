from __future__ import annotations

import logging
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..domain import JobStatus, Lesson
from ..recording import RecordingResult

RecordingDiscoverer = Callable[[Path], Iterable[Path]]
RecordingRecoverer = Callable[[Path], RecordingResult]
RecordingLessonLookup = Callable[[str], Lesson | None]
RecordingLessonSaver = Callable[[Lesson, tuple[str, ...]], object]
RecordingRecoveryFinalizer = Callable[[RecordingResult, Lesson], RecordingResult]


class RecordingRecoveryState(StrEnum):
    """Application outcome for a persisted recording-recovery attempt."""

    RECOVERED = "recovered"
    AUDIO_ONLY = "audio_only"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RecordingRecoveryOutcome:
    """Typed result returned to the presentation adapter after recovery."""

    state: RecordingRecoveryState
    recording_dir: Path
    result: RecordingResult | None = None
    lesson: Lesson | None = None
    error: str | None = None

    @classmethod
    def recovered(
        cls,
        recording_dir: Path,
        result: RecordingResult,
        lesson: Lesson,
    ) -> RecordingRecoveryOutcome:
        return cls(
            state=RecordingRecoveryState.RECOVERED,
            recording_dir=recording_dir,
            result=result,
            lesson=lesson,
        )

    @classmethod
    def audio_only(
        cls,
        recording_dir: Path,
        result: RecordingResult,
    ) -> RecordingRecoveryOutcome:
        return cls(
            state=RecordingRecoveryState.AUDIO_ONLY,
            recording_dir=recording_dir,
            result=result,
        )

    @classmethod
    def failed(
        cls,
        recording_dir: Path,
        *,
        result: RecordingResult | None,
        lesson: Lesson | None,
        error: str,
    ) -> RecordingRecoveryOutcome:
        return cls(
            state=RecordingRecoveryState.FAILED,
            recording_dir=recording_dir,
            result=result,
            lesson=lesson,
            error=error,
        )


class RecoverRecordingUseCase:
    """Recover a WAV-first recording and reconcile it with the lesson record.

    The recording infrastructure owns the canonical ``session.json`` state machine.
    In particular, successful recovery finishes with ``status=completed``; this use
    case deliberately does not introduce a presentation-only ``recovered`` session
    status. If the lesson still exists, the recovered delivery audio is renamed by
    the injected finalizer, attached to the lesson and persisted.

    Recovery of the raw audio is useful even when the lesson record is unavailable,
    so missing lesson metadata is a successful ``AUDIO_ONLY`` outcome rather than an
    error. Metadata lookup happens only after the durable audio recovery: a database
    failure therefore cannot prevent the WAV chunks from being rescued, while still
    being reported as ``FAILED`` instead of being confused with a missing lesson.
    """

    _RECOVERABLE_LESSON_STATUSES = frozenset(
        {
            JobStatus.DRAFT,
            JobStatus.RECORDING,
            JobStatus.RECORDED,
            JobStatus.FAILED,
        }
    )

    def __init__(
        self,
        *,
        discoverer: RecordingDiscoverer,
        recoverer: RecordingRecoverer,
        lesson_lookup: RecordingLessonLookup,
        lesson_saver: RecordingLessonSaver,
        result_finalizer: RecordingRecoveryFinalizer,
    ) -> None:
        self._discoverer = discoverer
        self._recoverer = recoverer
        self._lesson_lookup = lesson_lookup
        self._lesson_saver = lesson_saver
        self._result_finalizer = result_finalizer

    def discover(self, workspace: Path) -> tuple[Path, ...]:
        """Return recoverable recording directories in infrastructure order."""

        return tuple(self._discoverer(workspace))

    def recover(self, recording_dir: Path) -> RecordingRecoveryOutcome:
        """Recover one recording without leaking persistence decisions into Qt."""

        recording_dir = recording_dir.resolve()
        lesson_id = recording_dir.parent.name
        result: RecordingResult | None = None

        try:
            result = self._recoverer(recording_dir)
        except Exception:
            details = traceback.format_exc()
            logging.error(
                "Recording recovery failed before durable result: lesson=%s dir=%s\n%s",
                lesson_id,
                recording_dir,
                details,
            )
            return RecordingRecoveryOutcome.failed(
                recording_dir,
                result=None,
                lesson=None,
                error=details,
            )

        try:
            lesson = self._lesson_lookup(lesson_id)
        except Exception:
            details = traceback.format_exc()
            logging.error(
                "Recovered audio could not load lesson metadata: lesson=%s\n%s",
                lesson_id,
                details,
            )
            return RecordingRecoveryOutcome.failed(
                recording_dir,
                result=result,
                lesson=None,
                error=details,
            )

        if lesson is None:
            logging.warning(
                "Recovered recording has no lesson metadata: lesson=%s dir=%s",
                lesson_id,
                recording_dir,
            )
            return RecordingRecoveryOutcome.audio_only(recording_dir, result)

        try:
            result = self._result_finalizer(result, lesson)
            lesson.source_audio_local = str(result.mixed_file.resolve())
            fields: tuple[str, ...]
            if lesson.status in self._RECOVERABLE_LESSON_STATUSES:
                lesson.transition(JobStatus.RECORDED)
                fields = ("source_audio_local", "status", "error")
            else:
                # Do not roll a lesson backwards if a stale recovery artifact is
                # encountered after transcription/review has already progressed.
                fields = ("source_audio_local",)
            self._lesson_saver(lesson, fields)
        except Exception:
            details = traceback.format_exc()
            logging.error(
                "Recovered audio could not be reconciled with lesson=%s\n%s",
                lesson_id,
                details,
            )
            self._mark_failed_if_unfinished(lesson, details)
            return RecordingRecoveryOutcome.failed(
                recording_dir,
                result=result,
                lesson=lesson,
                error=details,
            )

        return RecordingRecoveryOutcome.recovered(recording_dir, result, lesson)

    def _mark_failed_if_unfinished(self, lesson: Lesson, details: str) -> None:
        if lesson.status not in self._RECOVERABLE_LESSON_STATUSES:
            return
        try:
            lesson.transition(JobStatus.FAILED, details[-2000:])
            self._lesson_saver(lesson, ("status", "error"))
        except Exception:
            logging.exception(
                "Не удалось сохранить ошибку восстановления: lesson=%s",
                lesson.lesson_id,
            )
