from __future__ import annotations

from dataclasses import replace

import pytest

from tutor_assistant.application.recording_health import (
    RecordingHealthMonitor,
    RecordingHealthPolicy,
    RecordingHealthSample,
)
from tutor_assistant.ui.recording_presentation import (
    RecordingPanelPhase,
    build_recording_tick_presentation,
    format_recording_duration,
    normalize_level_percent,
    recording_panel_visual,
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
        reconnect_attempts=2,
    )
    return replace(baseline, **changes)


def monitor() -> RecordingHealthMonitor:
    return RecordingHealthMonitor(
        RecordingHealthPolicy(
            device_timeout_seconds=5,
            silence_warning_seconds=20,
        )
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00"),
        (59, "00:00:59"),
        (60, "00:01:00"),
        (3661, "01:01:01"),
        (-1, "00:00:00"),
    ],
)
def test_duration_formatting_is_presentation_owned(seconds: int, expected: str) -> None:
    assert format_recording_duration(seconds) == expected


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (-0.4, 0),
        (0.0, 0),
        (0.214, 21),
        (1.0, 100),
        (1.7, 100),
    ],
)
def test_level_normalization_clamps_to_progress_bar_range(
    level: float,
    expected: int,
) -> None:
    assert normalize_level_percent(level) == expected


def test_live_presentation_formats_metrics_without_qt() -> None:
    assessment = monitor().assess(sample())

    presentation = build_recording_tick_presentation(3661, assessment)

    assert presentation.duration_text == "01:01:01"
    assert presentation.microphone_level_percent == 21
    assert presentation.system_level_percent == 34
    assert presentation.health_text == (
        "Очереди: 4% / 7%; потеряно блоков: 0; "
        "задержка writer: 12.5 мс; тишина: 0 / 0 с; переподключения: 2"
    )
    assert presentation.status_message is None
    assert presentation.status_tone is None
    assert presentation.warning_log is None


def test_inactive_tick_only_updates_duration() -> None:
    presentation = build_recording_tick_presentation(7, None)

    assert presentation.duration_text == "00:00:07"
    assert presentation.microphone_level_percent is None
    assert presentation.system_level_percent is None
    assert presentation.health_text is None


def test_new_warning_maps_to_warning_status_and_log_event() -> None:
    assessment = monitor().assess(sample(microphone_silence_seconds=20))

    presentation = build_recording_tick_presentation(20, assessment)

    assert presentation.status_message == "Проверьте аудио · микрофон молчит 20 с"
    assert presentation.status_tone == "warning"
    assert presentation.warning_log == "микрофон молчит 20 с"


def test_repeated_warning_does_not_repeat_status_or_log_event() -> None:
    health_monitor = monitor()
    health_monitor.assess(sample(microphone_silence_seconds=20))
    assessment = health_monitor.assess(sample(microphone_silence_seconds=20))

    presentation = build_recording_tick_presentation(21, assessment)

    assert presentation.status_message is None
    assert presentation.status_tone is None
    assert presentation.warning_log is None


def test_warning_recovery_maps_back_to_recording_status() -> None:
    health_monitor = monitor()
    health_monitor.assess(sample(system_dropped_blocks=1))
    assessment = health_monitor.assess(sample())

    presentation = build_recording_tick_presentation(22, assessment)

    assert presentation.status_message == "Идёт запись"
    assert presentation.status_tone == "working"
    assert presentation.warning_log is None


def test_terminal_stop_suppresses_non_terminal_warning_transition() -> None:
    assessment = monitor().assess(
        sample(
            microphone_silence_seconds=20,
            stream_errors=("microphone disconnected",),
        )
    )

    presentation = build_recording_tick_presentation(23, assessment)

    assert presentation.status_message is None
    assert presentation.status_tone is None
    assert presentation.warning_log is None


@pytest.mark.parametrize(
    ("phase", "text", "active"),
    [
        (RecordingPanelPhase.READY, "ГОТОВО К ЗАПИСИ", False),
        (RecordingPanelPhase.RECORDING, "●  ИДЁТ ЗАПИСЬ", True),
        (RecordingPanelPhase.SAVING, "СОХРАНЯЮ ЗАПИСЬ…", True),
        (RecordingPanelPhase.SAVED, "ЗАПИСЬ СОХРАНЕНА", False),
        (
            RecordingPanelPhase.RECOVERY_REQUIRED,
            "ЗАПИСЬ ТРЕБУЕТ ВОССТАНОВЛЕНИЯ",
            False,
        ),
        (RecordingPanelPhase.FAILED, "ЗАПИСЬ СОХРАНЕНА С ОШИБКОЙ", False),
    ],
)
def test_recording_panel_phase_has_one_canonical_visual_state(
    phase: RecordingPanelPhase,
    text: str,
    active: bool,
) -> None:
    visual = recording_panel_visual(phase)

    assert visual.text == text
    assert visual.active is active
