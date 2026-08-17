"""Regression gates for application-owned recording UI orchestration."""

from __future__ import annotations

import inspect
from pathlib import Path

from tutor_assistant.ui import app as base_app
from tutor_assistant.ui.audio_resilient_app import MainWindow as StartRecordingMainWindow
from tutor_assistant.ui.recording_finalize_app import MainWindow as StopRecordingMainWindow
from tutor_assistant.ui.recording_recovery_app import MainWindow as ProductionMainWindow

UI_ROOT = Path("src/tutor_assistant/ui")


def _source(name: str) -> str:
    return (UI_ROOT / name).read_text(encoding="utf-8")


def test_base_ui_contains_command_ports_not_recording_orchestration() -> None:
    source = _source("app.py")

    assert "def start_recording(self) -> None:" in source
    assert "def _stop_recording_async(self, reason: str | None = None) -> None:" in source
    assert "Recording start is owned by the production application adapter" in source
    assert "Recording stop/finalization is owned by the production application adapter" in source

    forbidden = (
        "find_recoverable_recordings",
        "recover_recording",
        "Worker(recorder.stop)",
        'acquire_activity(\n                "recording"',
        "def _recording_ready(",
        "def _recording_ready_impl(",
        "def _recording_stop_failed(",
        "def _recovery_ready(",
        "def _recovery_failed(",
    )
    for token in forbidden:
        assert token not in source


def test_shared_lesson_builder_is_persistence_free() -> None:
    builder = inspect.getsource(base_app.MainWindow._build_lesson_from_form)
    creator = inspect.getsource(base_app.MainWindow._create_lesson_from_form)

    assert "return Lesson(" in builder
    assert "pipeline.create" not in builder
    assert "self._build_lesson_from_form()" in creator
    assert "self.pipeline.create(lesson)" in creator


def test_intermediate_ui_layers_do_not_reintroduce_recording_callback_bridges() -> None:
    for name in ("concurrent_app.py", "transcript_publication_app.py"):
        source = _source(name)
        assert "def start_recording(" not in source
        assert "def _stop_recording_async(" not in source
        assert "def _recording_ready(" not in source
        assert "def _recording_ready_impl(" not in source
        assert "def _recording_stop_failed(" not in source
        assert "def _recovery_ready(" not in source


def test_start_adapter_is_the_only_pre_finalize_start_owner() -> None:
    source = _source("audio_resilient_app.py")

    assert "def start_recording(self) -> None:" in source
    assert "self.start_recording_use_case.start(" in source
    assert "recording_lesson = self._build_lesson_from_form()" in source
    assert "super().start_recording()" not in source
    assert "def _recording_ready(" not in source
    assert "def _recording_stop_failed(" not in source
    assert "def _recovery_ready(" not in source


def test_stop_and_recovery_owners_do_not_delegate_to_legacy_callbacks() -> None:
    stop_source = inspect.getsource(StopRecordingMainWindow._stop_recording_async)
    recovery_source = inspect.getsource(ProductionMainWindow._offer_recovery)

    assert "stop_recording_use_case.stop" in stop_source
    assert "super()._stop_recording_async" not in stop_source
    assert "recover_recording_use_case.discover" in recovery_source
    assert "super()._offer_recovery" not in recovery_source


def test_production_composition_contains_start_stop_and_recovery_owners() -> None:
    assert issubclass(ProductionMainWindow, StopRecordingMainWindow)
    assert issubclass(StopRecordingMainWindow, StartRecordingMainWindow)


def test_gui_entrypoint_uses_complete_recording_composition_root() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert (
        'tutor-assistant-gui = "tutor_assistant.ui.recording_recovery_app:main"'
        in pyproject
    )
