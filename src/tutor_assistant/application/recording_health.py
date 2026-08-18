from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .recording import RecordingHealthSnapshot, RecordingLevelsSnapshot


class RecordingHealthSeverity(StrEnum):
    """Application-level severity for one recorder-health assessment."""

    HEALTHY = "healthy"
    WARNING = "warning"
    TERMINAL = "terminal"


class RecordingHealthAction(StrEnum):
    """Action requested by the health policy without depending on UI transport."""

    NONE = "none"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class RecordingHealthPolicy:
    """Thresholds used to interpret recorder runtime telemetry."""

    device_timeout_seconds: float
    silence_warning_seconds: float

    def __post_init__(self) -> None:
        if self.device_timeout_seconds < 0:
            raise ValueError("device_timeout_seconds must be non-negative")
        if self.silence_warning_seconds < 0:
            raise ValueError("silence_warning_seconds must be non-negative")


@dataclass(frozen=True, slots=True)
class RecordingHealthSample:
    """Immutable recorder telemetry captured for one monitoring tick."""

    elapsed_seconds: float
    microphone_level: float
    system_level: float
    microphone_queue_percent: int
    system_queue_percent: int
    microphone_dropped_blocks: int
    system_dropped_blocks: int
    max_writer_latency_ms: float
    microphone_silence_seconds: float
    system_silence_seconds: float
    microphone_callback_age_seconds: float
    system_callback_age_seconds: float
    stream_errors: tuple[str, ...]
    reconnect_attempts: int

    @classmethod
    def from_runtime(
        cls,
        *,
        elapsed_seconds: float,
        levels: RecordingLevelsSnapshot,
        health: RecordingHealthSnapshot,
    ) -> RecordingHealthSample:
        return cls(
            elapsed_seconds=elapsed_seconds,
            microphone_level=levels.microphone,
            system_level=levels.system,
            microphone_queue_percent=health.microphone_queue_percent,
            system_queue_percent=health.system_queue_percent,
            microphone_dropped_blocks=health.microphone_dropped_blocks,
            system_dropped_blocks=health.system_dropped_blocks,
            max_writer_latency_ms=health.max_writer_latency_ms,
            microphone_silence_seconds=health.microphone_silence_seconds,
            system_silence_seconds=health.system_silence_seconds,
            microphone_callback_age_seconds=health.microphone_callback_age_seconds,
            system_callback_age_seconds=health.system_callback_age_seconds,
            stream_errors=tuple(health.stream_errors),
            reconnect_attempts=health.reconnect_attempts,
        )


@dataclass(frozen=True, slots=True)
class RecordingHealthAssessment:
    """Presentation-neutral interpretation of one recorder-health sample."""

    sample: RecordingHealthSample
    severity: RecordingHealthSeverity
    action: RecordingHealthAction
    warnings: tuple[str, ...]
    warning_changed: bool
    recovered_from_warning: bool
    stop_reason: str | None
    dropped_blocks: int

    @property
    def microphone_level_percent(self) -> int:
        return round(self.sample.microphone_level * 100)

    @property
    def system_level_percent(self) -> int:
        return round(self.sample.system_level * 100)

    @property
    def warning_text(self) -> str:
        return "; ".join(self.warnings)


class RecordingHealthMonitor:
    """Stateful, Qt-free policy engine for live recorder telemetry.

    The monitor owns warning de-duplication across ticks. Presentation adapters only
    render the returned assessment and execute the requested terminal action.
    """

    def __init__(self, policy: RecordingHealthPolicy) -> None:
        self._policy = policy
        self._active_warnings: tuple[str, ...] = ()

    @property
    def policy(self) -> RecordingHealthPolicy:
        return self._policy

    @property
    def active_warnings(self) -> tuple[str, ...]:
        return self._active_warnings

    def reset(self) -> None:
        self._active_warnings = ()

    def assess(self, sample: RecordingHealthSample) -> RecordingHealthAssessment:
        warnings = self._warnings(sample)
        previous_warnings = self._active_warnings
        warning_changed = warnings != previous_warnings
        recovered_from_warning = bool(previous_warnings) and not warnings
        self._active_warnings = warnings

        stop_reason = self._stop_reason(sample)
        if stop_reason is not None:
            severity = RecordingHealthSeverity.TERMINAL
            action = RecordingHealthAction.STOP
        elif warnings:
            severity = RecordingHealthSeverity.WARNING
            action = RecordingHealthAction.NONE
        else:
            severity = RecordingHealthSeverity.HEALTHY
            action = RecordingHealthAction.NONE

        return RecordingHealthAssessment(
            sample=sample,
            severity=severity,
            action=action,
            warnings=warnings,
            warning_changed=warning_changed,
            recovered_from_warning=recovered_from_warning,
            stop_reason=stop_reason,
            dropped_blocks=(
                sample.microphone_dropped_blocks + sample.system_dropped_blocks
            ),
        )

    def _stop_reason(self, sample: RecordingHealthSample) -> str | None:
        if sample.stream_errors:
            return "Ошибка аудиоустройства: " + "; ".join(sample.stream_errors)

        timeout = self._policy.device_timeout_seconds
        if sample.elapsed_seconds > timeout and (
            sample.microphone_callback_age_seconds > timeout
            or sample.system_callback_age_seconds > timeout
        ):
            return "Потерян поток аудиоустройства; сохранены доступные чанки записи"
        return None

    def _warnings(self, sample: RecordingHealthSample) -> tuple[str, ...]:
        warnings: list[str] = []
        silence_limit = self._policy.silence_warning_seconds
        if sample.microphone_silence_seconds >= silence_limit:
            warnings.append(
                f"микрофон молчит {sample.microphone_silence_seconds:.0f} с"
            )
        if sample.system_silence_seconds >= silence_limit:
            warnings.append(
                "звук ученика отсутствует "
                f"{sample.system_silence_seconds:.0f} с"
            )
        dropped = sample.microphone_dropped_blocks + sample.system_dropped_blocks
        if dropped:
            warnings.append(f"потеряно блоков: {dropped}")
        return tuple(warnings)
