from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..application.recording_health import RecordingHealthAssessment


class RecordingPanelPhase(StrEnum):
    """Presentation-only phases for the recording panel state label."""

    READY = "ready"
    RECORDING = "recording"
    SAVING = "saving"
    SAVED = "saved"
    RECOVERY_REQUIRED = "recovery_required"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RecordingPanelVisual:
    """Presentation data for the recording-state label."""

    text: str
    active: bool


@dataclass(frozen=True, slots=True)
class RecordingTickPresentation:
    """Pure view data for one recording timer tick."""

    duration_text: str
    microphone_level_percent: int | None = None
    system_level_percent: int | None = None
    health_text: str | None = None
    status_message: str | None = None
    status_tone: str | None = None
    warning_log: str | None = None


_PANEL_VISUALS = {
    RecordingPanelPhase.READY: RecordingPanelVisual("ГОТОВО К ЗАПИСИ", False),
    RecordingPanelPhase.RECORDING: RecordingPanelVisual("●  ИДЁТ ЗАПИСЬ", True),
    RecordingPanelPhase.SAVING: RecordingPanelVisual("СОХРАНЯЮ ЗАПИСЬ…", True),
    RecordingPanelPhase.SAVED: RecordingPanelVisual("ЗАПИСЬ СОХРАНЕНА", False),
    RecordingPanelPhase.RECOVERY_REQUIRED: RecordingPanelVisual(
        "ЗАПИСЬ ТРЕБУЕТ ВОССТАНОВЛЕНИЯ",
        False,
    ),
    RecordingPanelPhase.FAILED: RecordingPanelVisual(
        "ЗАПИСЬ СОХРАНЕНА С ОШИБКОЙ",
        False,
    ),
}


def recording_panel_visual(phase: RecordingPanelPhase) -> RecordingPanelVisual:
    return _PANEL_VISUALS[phase]


def format_recording_duration(elapsed_seconds: int) -> str:
    """Format the elapsed recording timer as HH:MM:SS."""

    elapsed = max(0, int(elapsed_seconds))
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def normalize_level_percent(level: float) -> int:
    """Convert an arbitrary normalized level into the 0..100 progress-bar range."""

    bounded = max(0.0, min(1.0, float(level)))
    return round(bounded * 100)


def build_recording_tick_presentation(
    elapsed_seconds: int,
    assessment: RecordingHealthAssessment | None,
) -> RecordingTickPresentation:
    """Build presentation-only text/levels/status for one timer tick."""

    duration_text = format_recording_duration(elapsed_seconds)
    if assessment is None:
        return RecordingTickPresentation(duration_text=duration_text)

    sample = assessment.sample
    warning_text = "; ".join(assessment.warnings)
    status_message: str | None = None
    status_tone: str | None = None
    warning_log: str | None = None
    if assessment.warning_changed and assessment.warnings:
        status_message = "Проверьте аудио · " + warning_text
        status_tone = "warning"
        warning_log = warning_text
    elif assessment.recovered_from_warning:
        status_message = "Идёт запись"
        status_tone = "working"

    return RecordingTickPresentation(
        duration_text=duration_text,
        microphone_level_percent=normalize_level_percent(sample.microphone_level),
        system_level_percent=normalize_level_percent(sample.system_level),
        health_text=(
            f"Очереди: {sample.microphone_queue_percent}% / "
            f"{sample.system_queue_percent}%; "
            f"потеряно блоков: {assessment.dropped_blocks}; "
            f"задержка writer: {sample.max_writer_latency_ms:.1f} мс; "
            f"тишина: {sample.microphone_silence_seconds:.0f} / "
            f"{sample.system_silence_seconds:.0f} с; "
            f"переподключения: {sample.reconnect_attempts}"
        ),
        status_message=status_message,
        status_tone=status_tone,
        warning_log=warning_log,
    )
