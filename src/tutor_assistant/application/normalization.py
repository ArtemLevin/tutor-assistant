from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NormalizationLifecycleState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"


class NormalizationStartBlock(StrEnum):
    NO_LESSON = "no_lesson"
    PROVIDER_ERROR = "provider_error"
    TRANSCRIPTION_BUSY = "transcription_busy"
    ALREADY_RUNNING = "already_running"
    NO_SEGMENTS = "no_segments"


class NormalizationAutoAction(StrEnum):
    IDLE = "idle"
    WAITING_CLOUD_CONSENT = "waiting_cloud_consent"
    START = "start"


class NormalizationAfterWorkerAction(StrEnum):
    PUMP_AUTO = "pump_auto"
    RETRY_INDETERMINATE = "retry_indeterminate"


@dataclass(frozen=True, slots=True)
class NormalizationManualStartContext:
    lesson_id: str | None
    provider: str
    provider_error: str | None
    has_segments: bool
    transcription_busy: bool


@dataclass(frozen=True, slots=True)
class NormalizationStartDecision:
    allowed: bool
    block: NormalizationStartBlock | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizationAutoContext:
    provider: str
    shutdown_requested: bool
    transcription_busy: bool


@dataclass(frozen=True, slots=True)
class NormalizationAutoDecision:
    action: NormalizationAutoAction
    lesson_id: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizationProgressSnapshot:
    current_chunk: int | None
    total_chunks: int
    completed_chunks: int
    reused_chunks: int
    provider_requests: int
    current_attempt: int | None
    state: str


class NormalizationCoordinator:
    """Qt-free lifecycle and scheduling policy for transcript normalization."""

    def __init__(self) -> None:
        self._state = NormalizationLifecycleState.IDLE
        self._active_lesson_id: str | None = None
        self._auto_queue: list[str] = []
        self._retry_indeterminate = False
        self._progress: NormalizationProgressSnapshot | None = None

    @property
    def state(self) -> NormalizationLifecycleState:
        return self._state

    @property
    def active(self) -> bool:
        return self._state != NormalizationLifecycleState.IDLE

    @property
    def active_lesson_id(self) -> str | None:
        return self._active_lesson_id

    @property
    def pending_auto_count(self) -> int:
        return len(self._auto_queue)

    @property
    def progress(self) -> NormalizationProgressSnapshot | None:
        return self._progress

    def evaluate_manual_start(
        self,
        context: NormalizationManualStartContext,
    ) -> NormalizationStartDecision:
        if context.lesson_id is None:
            return NormalizationStartDecision(False, NormalizationStartBlock.NO_LESSON)
        if context.provider_error:
            return NormalizationStartDecision(
                False,
                NormalizationStartBlock.PROVIDER_ERROR,
                context.provider_error,
            )
        if context.provider == "ollama" and context.transcription_busy:
            return NormalizationStartDecision(False, NormalizationStartBlock.TRANSCRIPTION_BUSY)
        if self.active:
            return NormalizationStartDecision(False, NormalizationStartBlock.ALREADY_RUNNING)
        if not context.has_segments:
            return NormalizationStartDecision(False, NormalizationStartBlock.NO_SEGMENTS)
        return NormalizationStartDecision(True)

    def begin(self, lesson_id: str) -> None:
        if self.active:
            raise RuntimeError("Нормализация уже выполняется")
        self._state = NormalizationLifecycleState.RUNNING
        self._active_lesson_id = lesson_id
        self._progress = None

    def enqueue_auto(self, lesson_id: str) -> bool:
        if lesson_id == self._active_lesson_id or lesson_id in self._auto_queue:
            return False
        self._auto_queue.append(lesson_id)
        return True

    def pump_auto(self, context: NormalizationAutoContext) -> NormalizationAutoDecision:
        if not self._auto_queue:
            return NormalizationAutoDecision(NormalizationAutoAction.IDLE)
        if context.provider == "yandex_ai_studio":
            return NormalizationAutoDecision(NormalizationAutoAction.WAITING_CLOUD_CONSENT)
        if context.shutdown_requested or self.active:
            return NormalizationAutoDecision(NormalizationAutoAction.IDLE)
        if context.provider == "ollama" and context.transcription_busy:
            return NormalizationAutoDecision(NormalizationAutoAction.IDLE)
        lesson_id = self._auto_queue.pop(0)
        self.begin(lesson_id)
        return NormalizationAutoDecision(NormalizationAutoAction.START, lesson_id)

    def update_progress(self, progress) -> NormalizationProgressSnapshot:
        snapshot = NormalizationProgressSnapshot(
            current_chunk=progress.current_chunk,
            total_chunks=progress.total_chunks,
            completed_chunks=progress.completed_chunks,
            reused_chunks=progress.reused_chunks,
            provider_requests=progress.provider_requests,
            current_attempt=progress.current_attempt,
            state=progress.state,
        )
        self._progress = snapshot
        return snapshot

    def request_cancel(self) -> bool:
        if not self.active:
            return False
        self._state = NormalizationLifecycleState.CANCELLING
        return True

    def record_resume_confirmation(self, retry_indeterminate: bool) -> None:
        self._retry_indeterminate = retry_indeterminate

    def finish_worker(self) -> NormalizationAfterWorkerAction:
        self._state = NormalizationLifecycleState.IDLE
        self._active_lesson_id = None
        self._progress = None
        if self._retry_indeterminate:
            self._retry_indeterminate = False
            return NormalizationAfterWorkerAction.RETRY_INDETERMINATE
        return NormalizationAfterWorkerAction.PUMP_AUTO
