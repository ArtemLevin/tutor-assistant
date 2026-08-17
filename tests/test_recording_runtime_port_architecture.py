"""Architecture gates for runtime recorder ownership."""

from __future__ import annotations

import inspect
from pathlib import Path

from tutor_assistant import application
from tutor_assistant.application.recording import (
    RecordingHealthSnapshot,
    RecordingLevelsSnapshot,
    RecordingRuntimeRecorder,
)
from tutor_assistant.ui import app as base_app
from tutor_assistant.ui.audio_resilient_app import MainWindow as ProductionAudioMainWindow


def test_application_runtime_recorder_port_is_qt_and_infrastructure_independent() -> None:
    application_source = Path("src/tutor_assistant/application/recording.py").read_text(
        encoding="utf-8"
    )
    levels = inspect.getsource(RecordingLevelsSnapshot)
    health = inspect.getsource(RecordingHealthSnapshot)
    runtime = inspect.getsource(RecordingRuntimeRecorder)

    assert "PySide6" not in application_source
    assert "DualRecorder" not in application_source
    assert "def microphone(self) -> float" in levels
    assert "def system(self) -> float" in levels
    assert "def stream_errors(self) -> tuple[str, ...]" in health
    assert "def reconnect_attempts(self) -> int" in health
    assert "RecordingRecorder" in runtime
    assert "def levels(self) -> RecordingLevelsSnapshot" in runtime
    assert "def health(self) -> RecordingHealthSnapshot" in runtime


def test_runtime_recorder_contract_is_exported_by_application_package() -> None:
    assert application.RecordingLevelsSnapshot is RecordingLevelsSnapshot
    assert application.RecordingHealthSnapshot is RecordingHealthSnapshot
    assert application.RecordingRuntimeRecorder is RecordingRuntimeRecorder


def test_base_ui_has_no_concrete_dual_recorder_dependency() -> None:
    source = Path("src/tutor_assistant/ui/app.py").read_text(encoding="utf-8")

    assert "DualRecorder" not in source
    assert "RecordingRuntimeRecorder" in source
    assert "self.recorder: RecordingRuntimeRecorder | None = None" in source


def test_publication_adapter_no_longer_monkeypatches_base_recorder() -> None:
    source = Path("src/tutor_assistant/ui/transcript_publication_app.py").read_text(
        encoding="utf-8"
    )

    assert "DualRecorder" not in source
    assert "base_app.DualRecorder" not in source
    assert "_create_configured_recorder" not in source


def test_production_audio_adapter_owns_live_recorder_construction() -> None:
    method = inspect.getsource(ProductionAudioMainWindow._create_live_recorder)

    assert "DualRecorder(" in method
    assert "output_format=self.config.recording.output_format" in method
    assert "_create_configured_recorder" not in method


def test_base_tick_depends_on_runtime_snapshots_not_concrete_recorder() -> None:
    method = inspect.getsource(base_app.MainWindow._tick)

    assert "self.recorder.levels" in method
    assert "self.recorder.health" in method
    assert "DualRecorder" not in method
