from __future__ import annotations

import inspect
from pathlib import Path

from tutor_assistant.ui import app as base_app


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_normalization_coordinator_is_qt_free() -> None:
    source = _source("src/tutor_assistant/application/normalization.py")

    assert "PySide6" not in source
    assert "QMessageBox" not in source
    assert "NormalizationService" not in source
    assert "Ollama" not in source
    assert "Yandex" not in source


def test_normalization_presentation_is_qt_free() -> None:
    source = _source("src/tutor_assistant/ui/normalization_presentation.py")

    assert "PySide6" not in source
    assert "QMessageBox" not in source
    assert "TranscriptWorkspace" not in source


def test_base_ui_delegates_normalization_start_policy() -> None:
    source = inspect.getsource(base_app.MainWindow.normalize_current_transcript)

    assert "evaluate_manual_start(" in source
    assert "NormalizationManualStartContext(" in source
    assert "self.normalization_coordinator.begin(" in source
    assert "self._normalization_cancellation is not None" not in source
    assert "provider == \"ollama\" and (" not in source


def test_auto_normalization_queue_is_owned_by_coordinator() -> None:
    app_source = _source("src/tutor_assistant/ui/app.py")
    pump_source = inspect.getsource(base_app.MainWindow._pump_auto_normalization)

    assert "_pending_auto_normalizations" not in app_source
    assert "normalization_coordinator.enqueue_auto(" in app_source
    assert "normalization_coordinator.pump_auto(" in pump_source
    assert "NormalizationAutoContext(" in pump_source


def test_normalization_controls_use_typed_presentation() -> None:
    source = inspect.getsource(base_app.MainWindow._sync_normalization_controls)

    assert "build_normalization_controls(" in source
    assert "NormalizationControlContext(" in source
    assert "process_detail.text()" not in source
    assert "NormalizationRunStatus.FAILED" not in source
    assert "NormalizationRunStatus.CANCELLED" not in source


def test_progress_cancel_resume_and_finish_use_coordinator_state() -> None:
    progress = inspect.getsource(base_app.MainWindow._normalization_progress_updated)
    cancel = inspect.getsource(base_app.MainWindow.cancel_normalization)
    resume = inspect.getsource(base_app.MainWindow._normalization_resume_confirmation_required)
    finished = inspect.getsource(base_app.MainWindow._normalization_worker_finished)

    assert "normalization_coordinator.update_progress(" in progress
    assert "normalization_coordinator.request_cancel(" in cancel
    assert "normalization_coordinator.record_resume_confirmation(" in resume
    assert "normalization_coordinator.finish_worker(" in finished
    assert "_retry_indeterminate_after_worker" not in finished


def test_result_and_failure_copy_are_mapped_outside_base_window() -> None:
    ready = inspect.getsource(base_app.MainWindow._normalization_ready)
    failed = inspect.getsource(base_app.MainWindow._normalization_failed)

    assert "build_normalization_ready_presentation(" in ready
    assert "retained_ratio * 100" not in ready
    assert "build_normalization_failure_presentation(" in failed
    assert "details.splitlines()" not in failed
