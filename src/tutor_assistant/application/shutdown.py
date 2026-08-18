from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ShutdownPhase(StrEnum):
    IDLE = "idle"
    DRAINING = "draining"
    READY = "ready"


class ShutdownCloseAction(StrEnum):
    ACCEPT = "accept"
    PROMPT = "prompt"
    TRY_IMMEDIATE = "try_immediate"
    IGNORE = "ignore"


class ShutdownDrainAction(StrEnum):
    NONE = "none"
    WAIT = "wait"
    SCHEDULE_CLOSE = "schedule_close"


@dataclass(frozen=True, slots=True)
class ShutdownRuntimeSnapshot:
    recording_active: bool = False
    recording_stop_in_flight: bool = False
    workers_running: bool = False
    transcription_busy: bool = False
    transcription_running: bool = False
    normalization_cancellable: bool = False

    @property
    def recording_busy(self) -> bool:
        return self.recording_active or self.recording_stop_in_flight

    @property
    def prompt_required(self) -> bool:
        return self.recording_busy or self.workers_running or self.transcription_busy

    @property
    def drain_busy(self) -> bool:
        return self.recording_busy or self.workers_running or self.transcription_running


@dataclass(frozen=True, slots=True)
class ShutdownCloseDecision:
    action: ShutdownCloseAction
    transcription_wait_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ShutdownDrainPlan:
    begin_draining: bool = False
    cancel_normalization: bool = False
    shutdown_transcription: bool = False
    quiesce_runtime: bool = False
    finalize_recording: bool = False


class ShutdownCoordinator:
    """Qt-free lifecycle policy for safe application shutdown."""

    def __init__(self, *, immediate_wait_ms: int = 1000) -> None:
        if immediate_wait_ms < 0:
            raise ValueError("immediate_wait_ms must be non-negative")
        self._phase = ShutdownPhase.IDLE
        self._immediate_wait_ms = immediate_wait_ms

    @property
    def phase(self) -> ShutdownPhase:
        return self._phase

    @property
    def draining(self) -> bool:
        return self._phase == ShutdownPhase.DRAINING

    @property
    def ready(self) -> bool:
        return self._phase == ShutdownPhase.READY

    def request_close(self, snapshot: ShutdownRuntimeSnapshot) -> ShutdownCloseDecision:
        if self._phase == ShutdownPhase.READY:
            return ShutdownCloseDecision(ShutdownCloseAction.ACCEPT)
        if self._phase == ShutdownPhase.DRAINING:
            return ShutdownCloseDecision(ShutdownCloseAction.IGNORE)
        if snapshot.prompt_required:
            return ShutdownCloseDecision(ShutdownCloseAction.PROMPT)
        return ShutdownCloseDecision(
            ShutdownCloseAction.TRY_IMMEDIATE,
            transcription_wait_ms=self._immediate_wait_ms,
        )

    def complete_immediate_shutdown(self, *, transcription_stopped: bool) -> ShutdownPhase:
        if self._phase == ShutdownPhase.READY:
            return self._phase
        if self._phase == ShutdownPhase.DRAINING:
            return self._phase
        self._phase = (
            ShutdownPhase.READY
            if transcription_stopped
            else ShutdownPhase.DRAINING
        )
        return self._phase

    def confirm_close(
        self,
        snapshot: ShutdownRuntimeSnapshot,
        *,
        confirmed: bool,
    ) -> ShutdownDrainPlan:
        if not confirmed or self._phase != ShutdownPhase.IDLE:
            return ShutdownDrainPlan()
        self._phase = ShutdownPhase.DRAINING
        return ShutdownDrainPlan(
            begin_draining=True,
            cancel_normalization=snapshot.normalization_cancellable,
            shutdown_transcription=True,
            quiesce_runtime=True,
            finalize_recording=(
                snapshot.recording_active and not snapshot.recording_stop_in_flight
            ),
        )

    def observe_drain(self, snapshot: ShutdownRuntimeSnapshot) -> ShutdownDrainAction:
        if self._phase != ShutdownPhase.DRAINING:
            return ShutdownDrainAction.NONE
        if snapshot.drain_busy:
            return ShutdownDrainAction.WAIT
        self._phase = ShutdownPhase.READY
        return ShutdownDrainAction.SCHEDULE_CLOSE
