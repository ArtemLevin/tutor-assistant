from __future__ import annotations

from dataclasses import replace

import pytest

from tutor_assistant.application.recording_health import (
    RecordingHealthAction,
    RecordingHealthMonitor,
    RecordingHealthPolicy,
    RecordingHealthSample,
    RecordingHealthSeverity,
)


def sample(**changes: object) -> RecordingHealthSample:
    baseline = RecordingHealthSample(
        elapsed_seconds=30,
        microphone_level=0.21,
        system_level=0.34,
        microphone_queue_percent=4,
        system_queue_percent=7,
        microphone_dropped_blocks=0,
        system_dropped_blocks=0,
        max_writer_latency_ms=12.5,
        microphone_silence_seconds=0,
        system_silence_seconds=0,
        microphone_callback_age_seconds=0.1,
        system_callback_age_seconds=0.2,
        stream_errors=(),
        reconnect_attempts=0,
    )
    return replace(baseline, **changes)


def monitor() -> RecordingHealthMonitor:
    return RecordingHealthMonitor(
        RecordingHealthPolicy(
            device_timeout_seconds=5,
            silence_warning_seconds=20,
        )
    )


def test_healthy_sample_is_non_terminal_and_preserves_metrics() -> None:
    assessment = monitor().assess(sample(reconnect_attempts=2))

    assert assessment.severity == RecordingHealthSeverity.HEALTHY
    assert assessment.action == RecordingHealthAction.NONE
    assert assessment.stop_reason is None
    assert assessment.warnings == ()
    assert assessment.dropped_blocks == 0
    assert assessment.microphone_level_percent == 21
    assert assessment.system_level_percent == 34
    assert assessment.sample.reconnect_attempts == 2


def test_microphone_silence_is_warning_only() -> None:
    assessment = monitor().assess(sample(microphone_silence_seconds=20))

    assert assessment.severity == RecordingHealthSeverity.WARNING
    assert assessment.action == RecordingHealthAction.NONE
    assert assessment.warnings == ("микрофон молчит 20 с",)
    assert assessment.warning_text == "микрофон молчит 20 с"


def test_system_silence_is_warning_only() -> None:
    assessment = monitor().assess(sample(system_silence_seconds=25.4))

    assert assessment.warnings == ("звук ученика отсутствует 25 с",)
    assert assessment.stop_reason is None


def test_simultaneous_silence_preserves_warning_order() -> None:
    assessment = monitor().assess(
        sample(
            microphone_silence_seconds=21,
            system_silence_seconds=22,
        )
    )

    assert assessment.warnings == (
        "микрофон молчит 21 с",
        "звук ученика отсутствует 22 с",
    )


def test_dropped_blocks_are_aggregated_as_warning() -> None:
    assessment = monitor().assess(
        sample(
            microphone_dropped_blocks=2,
            system_dropped_blocks=3,
        )
    )

    assert assessment.dropped_blocks == 5
    assert assessment.warnings == ("потеряно блоков: 5",)
    assert assessment.severity == RecordingHealthSeverity.WARNING


def test_stream_error_requests_terminal_stop_with_original_reason() -> None:
    assessment = monitor().assess(
        sample(stream_errors=("microphone disconnected", "system failed"))
    )

    assert assessment.severity == RecordingHealthSeverity.TERMINAL
    assert assessment.action == RecordingHealthAction.STOP
    assert assessment.stop_reason == (
        "Ошибка аудиоустройства: microphone disconnected; system failed"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"microphone_callback_age_seconds": 5.1},
        {"system_callback_age_seconds": 5.1},
    ],
)
def test_callback_timeout_requests_terminal_stop(changes: dict[str, object]) -> None:
    assessment = monitor().assess(sample(elapsed_seconds=6, **changes))

    assert assessment.action == RecordingHealthAction.STOP
    assert assessment.stop_reason == (
        "Потерян поток аудиоустройства; сохранены доступные чанки записи"
    )


def test_callback_timeout_does_not_fire_during_startup_grace_period() -> None:
    assessment = monitor().assess(
        sample(
            elapsed_seconds=5,
            microphone_callback_age_seconds=99,
            system_callback_age_seconds=99,
        )
    )

    assert assessment.action == RecordingHealthAction.NONE
    assert assessment.stop_reason is None


def test_same_warning_is_deduplicated_across_ticks() -> None:
    health_monitor = monitor()
    first = health_monitor.assess(sample(microphone_silence_seconds=20))
    second = health_monitor.assess(sample(microphone_silence_seconds=20))

    assert first.warning_changed is True
    assert second.warning_changed is False
    assert second.recovered_from_warning is False


def test_recovery_from_warning_is_explicit_transition() -> None:
    health_monitor = monitor()
    health_monitor.assess(sample(system_dropped_blocks=1))

    recovered = health_monitor.assess(sample())

    assert recovered.warning_changed is True
    assert recovered.recovered_from_warning is True
    assert recovered.warnings == ()
    assert health_monitor.active_warnings == ()


def test_reset_forgets_previous_recording_warning_state() -> None:
    health_monitor = monitor()
    health_monitor.assess(sample(microphone_silence_seconds=20))

    health_monitor.reset()
    healthy = health_monitor.assess(sample())

    assert healthy.warning_changed is False
    assert healthy.recovered_from_warning is False


def test_policy_rejects_negative_thresholds() -> None:
    with pytest.raises(ValueError, match="device_timeout_seconds"):
        RecordingHealthPolicy(-1, 20)
    with pytest.raises(ValueError, match="silence_warning_seconds"):
        RecordingHealthPolicy(5, -1)
