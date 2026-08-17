from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from ..domain import JobStatus, Lesson


class RecordingWorkflowPhase(StrEnum):
    """Application-level recording lifecycle independent from Qt widgets."""

    IDLE = "idle"
    PREPARING = "preparing"
    RECORDING = "recording"
    STOPPING = "stopping"
    RECOVERY_REQUIRED = "recovery_required"
    FAILED = "failed"


class RecordingWorkflowRejected(RuntimeError):
    """Raised when a recording command conflicts with the current runtime state."""


@dataclass(frozen=True, slots=True)
class RecordingRuntimeState:
    """Minimal runtime facts exposed by the presentation adapter."""

    active: bool = False
    stopping: bool = False
    shutdown_requested: bool = False


class RecordingWorkflowController:
    """Own recording command/lifecycle semantics outside the presentation layer.

    The first Wave-2 slice intentionally does not own sound devices or persistence.
    It establishes the application boundary and becomes the single place that
    decides whether start/stop commands are admissible and how runtime facts map to
    recording phases. Later slices can move recorder construction and persistence
    behind ports without changing the UI-facing contract.
    """

    _BUSY_PHASES = frozenset(
        {
            RecordingWorkflowPhase.PREPARING,
            RecordingWorkflowPhase.RECORDING,
            RecordingWorkflowPhase.STOPPING,
        }
    )

    def __init__(self) -> None:
        self._phase = RecordingWorkflowPhase.IDLE

    @property
    def phase(self) -> RecordingWorkflowPhase:
        return self._phase

    @property
    def busy(self) -> bool:
        return self._phase in self._BUSY_PHASES

    def begin_start(self, runtime: RecordingRuntimeState) -> None:
        """Validate a user start command and enter the preparation phase."""

        if runtime.shutdown_requested:
            raise RecordingWorkflowRejected("Приложение завершает фоновые операции")
        if runtime.stopping or runtime.active or self.busy:
            raise RecordingWorkflowRejected("Запись уже запущена или сохраняется")
        self._phase = RecordingWorkflowPhase.PREPARING

    def abort_start(self) -> None:
        """Return to idle when preparation/preflight did not start capture."""

        if self._phase == RecordingWorkflowPhase.PREPARING:
            self._phase = RecordingWorkflowPhase.IDLE

    def observe_runtime(self, runtime: RecordingRuntimeState) -> RecordingWorkflowPhase:
        """Synchronize transient workflow state with authoritative runtime facts."""

        if runtime.stopping:
            self._phase = RecordingWorkflowPhase.STOPPING
        elif runtime.active:
            self._phase = RecordingWorkflowPhase.RECORDING
        elif self._phase in self._BUSY_PHASES:
            self._phase = RecordingWorkflowPhase.IDLE
        return self._phase

    def begin_stop(self, runtime: RecordingRuntimeState) -> bool:
        """Request stop once; return False for duplicate/no-active-recording calls."""

        if runtime.stopping or self._phase == RecordingWorkflowPhase.STOPPING:
            self._phase = RecordingWorkflowPhase.STOPPING
            return False
        if not runtime.active:
            self.observe_runtime(runtime)
            return False
        self._phase = RecordingWorkflowPhase.STOPPING
        return True

    def mark_completed(self) -> None:
        self._phase = RecordingWorkflowPhase.IDLE

    def mark_failed(self) -> None:
        self._phase = RecordingWorkflowPhase.FAILED

    def mark_recovery_required(self) -> None:
        self._phase = RecordingWorkflowPhase.RECOVERY_REQUIRED

    def reset(self) -> None:
        self._phase = RecordingWorkflowPhase.IDLE


class RecordingLease(Protocol):
    """Minimal application contract for an acquired content activity lease."""

    def release(self) -> None: ...


class RecordingRecorder(Protocol):
    """Capture port required by the start-recording use case."""

    @property
    def active(self) -> bool: ...

    def start(self, output_dir: Path, mic_device: int, system_source: object) -> None: ...

    def stop(self) -> object: ...


class RecordingPipeline(Protocol):
    """Persistence port used while establishing a recording session."""

    def create(self, lesson: Lesson) -> Path: ...

    def lesson_dir(self, lesson: Lesson) -> Path: ...

    def save_state(self, lesson: Lesson, *fields: str, **kwargs: object) -> Lesson: ...


class RecordingActivityService(Protocol):
    """Activity-coordination port used to protect a live recording."""

    def acquire_activity(
        self,
        activity: str,
        *,
        lesson_id: str | None = None,
        exclusive: bool = False,
        ttl: timedelta = timedelta(minutes=2),
    ) -> RecordingLease: ...


@dataclass(frozen=True, slots=True)
class StartedRecording:
    """Application context handed back to the presentation/runtime adapter."""

    lesson: Lesson
    recorder: RecordingRecorder
    lease: RecordingLease
    directory: Path


class StartRecordingUseCase:
    """Establish a durable live-recording session as one application transaction.

    The use case preserves the historical ordering of side effects while moving
    their ownership out of the GUI: persist the lesson, acquire a recording lease,
    create a recorder, persist ``RECORDING``, then start capture. Any failure after
    lesson creation is compensated best-effort by stopping a partially active
    recorder, releasing the lease and persisting ``FAILED``.
    """

    def __init__(
        self,
        pipeline: RecordingPipeline,
        activities: RecordingActivityService,
        recorder_factory: Callable[[], RecordingRecorder],
        *,
        lease_ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        self._pipeline = pipeline
        self._activities = activities
        self._recorder_factory = recorder_factory
        self._lease_ttl = lease_ttl

    def start(
        self,
        lesson: Lesson,
        *,
        mic_device: int,
        system_source: object,
    ) -> StartedRecording:
        created = False
        lease: RecordingLease | None = None
        recorder: RecordingRecorder | None = None
        try:
            self._pipeline.create(lesson)
            created = True
            lease = self._activities.acquire_activity(
                "recording",
                lesson_id=lesson.lesson_id,
                ttl=self._lease_ttl,
            )
            directory = self._pipeline.lesson_dir(lesson) / "recording"
            recorder = self._recorder_factory()
            lesson.transition(JobStatus.RECORDING)
            self._pipeline.save_state(lesson, "status", "error")
            recorder.start(directory, mic_device, system_source)
            return StartedRecording(
                lesson=lesson,
                recorder=recorder,
                lease=lease,
                directory=directory,
            )
        except Exception as exc:
            self._rollback(
                lesson,
                error=exc,
                created=created,
                lease=lease,
                recorder=recorder,
            )
            raise

    def abort(self, started: StartedRecording, error: BaseException) -> None:
        """Compensate a session when presentation setup fails after capture began."""

        self._rollback(
            started.lesson,
            error=error,
            created=True,
            lease=started.lease,
            recorder=started.recorder,
        )

    def _rollback(
        self,
        lesson: Lesson,
        *,
        error: BaseException,
        created: bool,
        lease: RecordingLease | None,
        recorder: RecordingRecorder | None,
    ) -> None:
        if recorder is not None:
            try:
                if recorder.active:
                    recorder.stop()
            except Exception:
                logging.exception("Не удалось остановить recorder после ошибки запуска")
        if lease is not None:
            try:
                lease.release()
            except Exception:
                logging.exception("Не удалось освободить recording lease после ошибки запуска")
        if not created:
            return
        try:
            lesson.transition(JobStatus.FAILED, str(error))
            self._pipeline.save_state(lesson, "status", "error")
        except Exception:
            logging.exception("Не удалось сохранить ошибку запуска записи")
