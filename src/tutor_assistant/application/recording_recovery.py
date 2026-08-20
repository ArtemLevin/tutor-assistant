from __future__ import annotations

import json
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
        """Find incomplete capture plus completed audio missing lesson metadata."""

        sessions = list(self._discoverer(workspace))
        discovered = set(sessions)
        for manifest in sorted(workspace.glob("lessons/*/recording/session.json")):
            recording_dir = manifest.parent
            if recording_dir in discovered or self._completed_session(recording_dir) is None:
                continue
            lesson = self._lesson_lookup(recording_dir.parent.name)
            if lesson is None:
                continue
            existing_audio = Path(lesson.source_audio_local).is_file() if lesson.source_audio_local else False
            if existing_audio and lesson.status not in {JobStatus.DRAFT, JobStatus.RECORDING}:
                continue
            if self._completed_result(recording_dir) is None and not any(
                (recording_dir / "chunks").rglob("*.wav")
            ):
                continue
            sessions.append(recording_dir)
            discovered.add(recording_dir)
        return tuple(sessions)

    def recover(self, recording_dir: Path) -> RecordingRecoveryOutcome:
        """Recover one recording without leaking persistence decisions into Qt."""

        recording_dir = recording_dir.resolve()
        lesson_id = recording_dir.parent.name
        result: RecordingResult | None = None

        try:
            result = self._completed_result(recording_dir)
            if result is None:
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

    @staticmethod
    def _completed_session(recording_dir: Path) -> dict[str, object] | None:
        try:
            session = json.loads((recording_dir / "session.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(session, dict) or session.get("status") != "completed":
            return None
        return session

    @classmethod
    def _completed_result(cls, recording_dir: Path) -> RecordingResult | None:
        session = cls._completed_session(recording_dir)
        if session is None:
            return None

        output_name = session.get("readable_output_file") or session.get("output_file")
        if not isinstance(output_name, str) or not output_name or "/" in output_name or "\\" in output_name:
            return None
        mixed_file = recording_dir / output_name
        if not mixed_file.is_file():
            return None
        return RecordingResult(
            microphone_file=recording_dir / "microphone.wav",
            system_file=recording_dir / "system.wav",
            mixed_file=mixed_file,
            session_file=recording_dir / "session.json",
            sync_report=recording_dir / "sync_report.json",
            quality_report=recording_dir / "audio_quality_report.json",
        )

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
