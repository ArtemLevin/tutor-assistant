"""Architecture gates for application-owned live recording health policy."""

from __future__ import annotations

import inspect
from pathlib import Path

from tutor_assistant.application import recording_health
from tutor_assistant.ui import app as base_app


def test_recording_health_policy_is_qt_and_infrastructure_free() -> None:
    source = inspect.getsource(recording_health)

    assert "PySide6" not in source
    assert "DualRecorder" not in source
    assert "AppConfig" not in source
    assert "sounddevice" not in source
    assert "soundcard" not in source.lower()


def test_base_tick_consumes_health_assessment_instead_of_interpreting_raw_health() -> None:
    tick = inspect.getsource(base_app.MainWindow._tick)

    assert "RecordingHealthSample.from_runtime" in tick
    assert "self.recording_health_monitor.assess" in tick
    assert "RecordingHealthAction.STOP" in tick
    assert "assessment.stop_reason" in tick
    assert "assessment.warning_changed" in tick
    assert "assessment.recovered_from_warning" in tick

    forbidden = (
        "health.stream_errors",
        "microphone_callback_age_seconds",
        "system_callback_age_seconds",
        "device_timeout_seconds",
        "silence_warning_seconds",
        "microphone_dropped_blocks",
        "system_dropped_blocks",
    )
    for token in forbidden:
        assert token not in tick


def test_base_ui_configures_qt_free_monitor_once() -> None:
    init_source = inspect.getsource(base_app.MainWindow.__init__)

    assert "RecordingHealthMonitor(" in init_source
    assert "RecordingHealthPolicy(" in init_source
    assert "self.config.recording.device_timeout_seconds" in init_source
    assert "self.config.recording.silence_warning_seconds" in init_source


def test_base_ui_does_not_import_recording_health_from_infrastructure() -> None:
    source = Path("src/tutor_assistant/ui/app.py").read_text(encoding="utf-8")

    assert "from ..application import (" in source
    assert "RecordingHealthAction" in source
    assert "RecordingHealthMonitor" in source
    assert "RecordingHealthPolicy" in source
    assert "RecordingHealthSample" in source
    assert "from ..recording" not in source
