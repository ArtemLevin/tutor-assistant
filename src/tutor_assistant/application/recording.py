from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
