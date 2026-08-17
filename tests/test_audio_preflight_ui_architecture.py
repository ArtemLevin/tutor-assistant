"""Architecture gates for the application-owned audio-preflight boundary."""

from __future__ import annotations

import inspect
from pathlib import Path

from tutor_assistant.ui import app as base_app
from tutor_assistant.ui.audio_resilient_app import MainWindow as ProductionAudioMainWindow


def test_base_ui_exposes_preflight_command_port_without_capture_orchestration() -> None:
    method = inspect.getsource(base_app.MainWindow._begin_preflight)
    source = Path("src/tutor_assistant/ui/app.py").read_text(encoding="utf-8")

    assert "raise NotImplementedError" in method
    assert "Audio preflight is owned by the production audio application adapter" in method
    assert "Worker(" not in method
    assert "DualRecorder(" not in method
    assert "sleep(" not in method
    assert "quality_report" not in method

    assert "def _device_test_ready(self, results)" not in source
    assert "from time import sleep" not in source


def test_device_test_action_still_routes_through_preflight_command_port() -> None:
    method = inspect.getsource(base_app.MainWindow.test_devices)

    assert "self._begin_preflight(show_intro=True)" in method


def test_base_preflight_playback_remains_presentation_only() -> None:
    method = inspect.getsource(base_app.MainWindow._play_preflight_track)

    assert "self.preflight_result.microphone_file" in method
    assert "self.preflight_result.system_file" in method
    assert "self.playback_controller.play_file" in method
    assert "quality_report" not in method
    assert "json.loads" not in method


def test_production_audio_adapter_owns_preflight_transport_and_result_presentation() -> None:
    begin = inspect.getsource(ProductionAudioMainWindow._begin_preflight)
    ready = inspect.getsource(ProductionAudioMainWindow._device_test_ready)

    assert "self.audio_preflight_use_case.run" in begin
    assert "base_app.Worker(" in begin
    assert "super()._begin_preflight" not in begin

    assert "AudioPreflightResult" in ready
    assert "result.microphone_rms" in ready
    assert "result.system_rms" in ready
    assert "result.quality_report" in ready
    assert "json.loads" not in ready
