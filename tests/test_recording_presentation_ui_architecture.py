"""Architecture gates for Wave 2 / Slice 11 recording presentation extraction."""

from __future__ import annotations

import inspect
from pathlib import Path

from tutor_assistant.application import recording_health
from tutor_assistant.ui import app as base_app
from tutor_assistant.ui import recording_presentation
from tutor_assistant.ui.audio_resilient_app import MainWindow as StartRecordingMainWindow

UI_ROOT = Path("src/tutor_assistant/ui")


def test_recording_presentation_model_is_qt_and_infrastructure_free() -> None:
    source = inspect.getsource(recording_presentation)

    assert "PySide6" not in source
    assert "DualRecorder" not in source
    assert "AppConfig" not in source
    assert "sounddevice" not in source
    assert "soundcard" not in source.lower()


def test_application_health_assessment_has_no_view_formatting_helpers() -> None:
    source = inspect.getsource(recording_health.RecordingHealthAssessment)

    assert "microphone_level_percent" not in source
    assert "system_level_percent" not in source
    assert "warning_text" not in source


def test_base_tick_delegates_view_formatting_to_presentation_model() -> None:
    tick = inspect.getsource(base_app.MainWindow._tick)

    assert "build_recording_tick_presentation" in tick
    assert "self._apply_recording_tick_presentation" in tick
    assert "RecordingHealthAction.STOP" in tick

    forbidden = (
        "divmod(",
        "self.duration.setText(",
        "self.mic_level.setValue(",
        "self.system_level.setValue(",
        "self.recording_health_label.setText(",
        "assessment.warning_changed",
        "assessment.recovered_from_warning",
        "assessment.warning_text",
        '"Проверьте аудио · "',
    )
    for token in forbidden:
        assert token not in tick


def test_base_renderer_only_applies_precomputed_view_data() -> None:
    renderer = inspect.getsource(base_app.MainWindow._apply_recording_tick_presentation)

    assert "presentation.duration_text" in renderer
    assert "presentation.microphone_level_percent" in renderer
    assert "presentation.system_level_percent" in renderer
    assert "presentation.health_text" in renderer
    assert "presentation.status_message" in renderer
    assert "presentation.status_tone" in renderer
    assert "presentation.warning_log" in renderer
    assert "RecordingHealthSample" not in renderer
    assert "RecordingHealthPolicy" not in renderer


def test_start_adapter_uses_canonical_recording_panel_phase() -> None:
    source = inspect.getsource(StartRecordingMainWindow._present_recording_started)

    assert "_set_recording_panel_phase(RecordingPanelPhase.RECORDING)" in source
    assert "recording_state_label.setText" not in source
    assert "recording_state_label.setProperty" not in source
    assert "refresh_style" not in source


def test_finalize_adapter_uses_canonical_panel_phases() -> None:
    source = (UI_ROOT / "recording_finalize_app.py").read_text(encoding="utf-8")

    for phase in ("SAVING", "SAVED", "RECOVERY_REQUIRED", "FAILED"):
        assert f"_set_recording_panel_phase(RecordingPanelPhase.{phase})" in source
    assert "recording_state_label.setText" not in source
    assert "recording_state_label.setProperty" not in source
    assert "refresh_style" not in source


def test_base_panel_phase_renderer_is_the_single_label_style_adapter() -> None:
    renderer = inspect.getsource(base_app.MainWindow._set_recording_panel_phase)

    assert "recording_panel_visual(phase)" in renderer
    assert "self.recording_state_label.setText(visual.text)" in renderer
    assert 'self.recording_state_label.setProperty("active", visual.active)' in renderer
    assert "refresh_style(self.recording_state_label)" in renderer
