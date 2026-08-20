from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..domain import JobStatus, Lesson
from ..recording import RecordingResult
from .recording import RecordingLease


class RecordingStopState(StrEnum):
    """Application outcome for stopping and finalizing a recording session."""

    RECORDED = "recorded"
    RECOVERY_REQUIRED = "recovery_required"
    FAILED = "failed"


class RecordingFinalizingRecorder(Protocol):
    """Recorder contract required by the stop/finalize use case."""

    @property
    def active(self) -> bool: ...

    @property
    def quiesced(self) -> bool: ...

    def stop(self) -> RecordingResult: ...


class RecordingStopPipeline(Protocol):
    """Persistence contract required after capture has stopped."""

    def save_state(self, lesson: Lesson, *fields: str, **kwargs: object) -> Lesson: ...

    def recording_lease_scope(
        self,
        lease: RecordingLease,
        *,
        lesson_id: str,
    ) -> AbstractContextManager[None]: ...


RecordingResultFinalizer = Callable[[RecordingResult, Lesson], RecordingResult]


@dataclass(frozen=True, slots=True)
class RecordingStopSession:
    """Stable application context captured before asynchronous stop begins."""

    lesson: Lesson
    recorder: RecordingFinalizingRecorder
    lease: RecordingLease | None


@dataclass(frozen=True, slots=True)
class RecordingStopOutcome:
    """Typed result separating recovery failures from finalization failures."""

    state: RecordingStopState
    lesson: Lesson
    recorder: RecordingFinalizingRecorder
    result: RecordingResult | None = None
    error: str | None = None

    @classmethod
    def recorded(
        cls,
        session: RecordingStopSession,
        result: RecordingResult,
    ) -> RecordingStopOutcome:
        return cls(
            state=RecordingStopState.RECORDED,
            lesson=session.lesson,
            recorder=session.recorder,
            result=result,
        )

    @classmethod
    def recovery_required(
        cls,
        session: RecordingStopSession,
        error: str,
    ) -> RecordingStopOutcome:
        return cls(
            state=RecordingStopState.RECOVERY_REQUIRED,
            lesson=session.lesson,
            recorder=session.recorder,
            error=error,
        )

    @classmethod
    def failed(
        cls,
        session: RecordingStopSession,
        *,
        result: RecordingResult | None,
        error: str,
    ) -> RecordingStopOutcome:
        return cls(
            state=RecordingStopState.FAILED,
            lesson=session.lesson,
            recorder=session.recorder,
            result=result,
            error=error,
        )


class StopRecordingUseCase:
    """Stop capture, finalize the delivery audio and persist the lesson atomically.

    Recorder-stop failures are intentionally distinguished from later finalization
    failures. A recorder-stop failure leaves the lesson in ``RECORDING`` so the
    persisted WAV chunks remain discoverable by recovery. Once ``recorder.stop()``
    has returned a durable result, any failure while naming/persisting the final
    audio marks the lesson ``FAILED`` instead: audio exists, but lesson finalization
    did not complete successfully.

    The recording activity lease is released only after the recorder confirms that
    all capture and writer resources are quiesced. This is deliberately stronger
    than checking ``active``: a failed stop must not make destructive/exclusive
    workspace operations possible while a WASAPI or writer thread can still touch
    the recording directory.
    """

    def __init__(
        self,
        pipeline: RecordingStopPipeline,
        *,
        result_finalizer: RecordingResultFinalizer | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._result_finalizer = result_finalizer or (lambda result, _lesson: result)

    def stop(self, session: RecordingStopSession) -> RecordingStopOutcome:
        result: RecordingResult | None = None
        try:
            try:
                result = session.recorder.stop()
                if not self._recorder_quiesced(session.recorder):
                    raise RuntimeError(
                        "Recorder вернул результат до полного завершения capture/writer ресурсов"
                    )
            except Exception:
                details = traceback.format_exc()
                logging.error(
                    "Recording stop requires recovery: lesson=%s\n%s",
                    session.lesson.lesson_id,
                    details,
                )
                return RecordingStopOutcome.recovery_required(session, details)

            try:
                lease_scope = (
                    self._pipeline.recording_lease_scope(
                        session.lease,
                        lesson_id=session.lesson.lesson_id,
                    )
                    if session.lease is not None
                    else nullcontext()
                )
                with lease_scope:
                    return self._finalize_stopped_recording(session, result)
            except Exception:
                details = traceback.format_exc()
                logging.error(
                    "Recording finalization failed: lesson=%s\n%s",
                    session.lesson.lesson_id,
                    details,
                )
                self._mark_failed(session.lesson, details)
                return RecordingStopOutcome.failed(
                    session,
                    result=result,
                    error=details,
                )
        finally:
            if self._recorder_quiesced(session.recorder):
                self._release_lease(session.lease, session.lesson.lesson_id)
            elif session.lease is not None:
                logging.critical(
                    "Recording lease retained because recorder is not quiesced: lesson=%s",
                    session.lesson.lesson_id,
                )

    def _finalize_stopped_recording(
        self,
        session: RecordingStopSession,
        result: RecordingResult,
    ) -> RecordingStopOutcome:
        try:
            result = self._result_finalizer(result, session.lesson)
            session.lesson.source_audio_local = str(result.mixed_file.resolve())
            session.lesson.transition(JobStatus.RECORDED)
            self._pipeline.save_state(
                session.lesson,
                "source_audio_local",
                "status",
                "error",
            )
            return RecordingStopOutcome.recorded(session, result)
        except Exception:
            details = traceback.format_exc()
            logging.error(
                "Recording finalization failed: lesson=%s\n%s",
                session.lesson.lesson_id,
                details,
            )
            self._mark_failed(session.lesson, details)
            return RecordingStopOutcome.failed(
                session,
                result=result,
                error=details,
            )

    @staticmethod
    def _recorder_quiesced(recorder: RecordingFinalizingRecorder) -> bool:
        explicit = getattr(recorder, "quiesced", None)
        if isinstance(explicit, bool):
            return explicit
        return not bool(getattr(recorder, "active", True))

    def _mark_failed(self, lesson: Lesson, details: str) -> None:
        try:
            lesson.transition(JobStatus.FAILED, details[-2000:])
            self._pipeline.save_state(lesson, "status", "error")
        except Exception:
            logging.exception(
                "Не удалось сохранить состояние ошибки финализации записи: lesson=%s",
                lesson.lesson_id,
            )

    @staticmethod
    def _release_lease(lease: RecordingLease | None, lesson_id: str) -> None:
        if lease is None:
            return
        try:
            lease.release()
        except Exception:
            logging.exception(
                "Не удалось освободить recording lease после завершения записи: lesson=%s",
                lesson_id,
            )
