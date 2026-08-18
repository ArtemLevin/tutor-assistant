from __future__ import annotations

import inspect
from pathlib import Path

from tutor_assistant.ui import app as base_app


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_shutdown_coordinator_is_qt_and_transport_free() -> None:
    source = _source("src/tutor_assistant/application/shutdown.py")

    assert "PySide6" not in source
    assert "QCloseEvent" not in source
    assert "QMessageBox" not in source
    assert "QTimer" not in source
    assert "TranscriptionWorker" not in source
    assert "RecordingRuntimeRecorder" not in source


def test_base_window_uses_coordinator_instead_of_legacy_shutdown_flags() -> None:
    source = _source("src/tutor_assistant/ui/app.py")

    assert "self.shutdown_coordinator = ShutdownCoordinator()" in source
    assert "_shutdown_requested" not in source
    assert "_shutdown_ready" not in source


def test_close_event_delegates_close_policy_and_confirmation() -> None:
    source = inspect.getsource(base_app.MainWindow.closeEvent)

    assert "self.shutdown_coordinator.request_close(" in source
    assert "self.shutdown_coordinator.confirm_close(" in source
    assert "ShutdownCloseAction.ACCEPT" in source
    assert "ShutdownCloseAction.PROMPT" in source
    assert "ShutdownCloseAction.TRY_IMMEDIATE" in source
    assert "any(worker.isRunning()" not in source
    assert "self.recorder and self.recorder.active" not in source
    assert "self.transcription_worker.busy" not in source


def test_runtime_snapshot_is_the_only_shutdown_transport_observer() -> None:
    source = inspect.getsource(base_app.MainWindow._shutdown_runtime_snapshot)

    assert "self.recorder and self.recorder.active" in source
    assert "self._recording_stop_started" in source
    assert "any(worker.isRunning()" in source
    assert "self.transcription_worker.busy" in source
    assert "self.transcription_worker.isRunning()" in source
    assert "self._normalization_cancellation is not None" in source


def test_drain_completion_delegates_barrier_policy() -> None:
    source = inspect.getsource(base_app.MainWindow._maybe_finish_shutdown)

    assert "self.shutdown_coordinator.observe_drain(" in source
    assert "ShutdownDrainAction.SCHEDULE_CLOSE" in source
    assert "recording_busy" not in source
    assert "workers_busy" not in source
    assert "self.transcription_worker.isRunning()" not in source


def test_confirmed_drain_side_effects_are_guarded_by_typed_plan() -> None:
    source = inspect.getsource(base_app.MainWindow._begin_shutdown_drain)

    assert "plan.cancel_normalization" in source
    assert "plan.shutdown_transcription" in source
    assert "plan.quiesce_runtime" in source
    assert "plan.finalize_recording" in source
    assert "self._normalization_cancellation.cancel()" in source
    assert "self._stop_recording_async(" in source


def test_shutdown_path_does_not_clear_persisted_transcription_queue() -> None:
    close_source = inspect.getsource(base_app.MainWindow.closeEvent)
    drain_source = inspect.getsource(base_app.MainWindow._begin_shutdown_drain)

    combined = close_source + drain_source
    assert "transcription_queue_coordinator.discard" not in combined
    assert ".clear()" not in combined
    assert "pending" not in combined.casefold()
