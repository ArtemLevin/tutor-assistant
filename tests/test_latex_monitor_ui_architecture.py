from __future__ import annotations

import inspect
from pathlib import Path

from tutor_assistant.ui import app as base_app


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_latex_monitor_coordinator_is_qt_and_infrastructure_free() -> None:
    source = _source("src/tutor_assistant/application/latex_monitor.py")

    assert "PySide6" not in source
    assert "QTimer" not in source
    assert "Worker" not in source
    assert "RemoteLatexService" not in source
    assert "LatexCompiler" not in source


def test_latex_monitor_presentation_is_qt_free() -> None:
    source = _source("src/tutor_assistant/ui/latex_monitor_presentation.py")

    assert "PySide6" not in source
    assert "QMessageBox" not in source
    assert "RemoteLatexService" not in source


def test_toggle_and_scan_delegate_lifecycle_to_coordinator() -> None:
    toggle = inspect.getsource(base_app.MainWindow.toggle_latex_monitor)
    scan = inspect.getsource(base_app.MainWindow.scan_remote_latex)

    assert "latex_monitor_coordinator.set_enabled(" in toggle
    assert "build_latex_monitor_toggle_presentation(" in toggle
    assert "latex_monitor_coordinator.request_scan(" in scan
    assert "LatexMonitorScanTrigger." in scan
    assert 'getattr(worker, "purpose", "")' not in scan
    assert 'worker.purpose = "latex-monitor"' not in scan


def test_remote_outcomes_use_typed_presentation_and_preserve_persistence() -> None:
    ready = inspect.getsource(base_app.MainWindow._remote_compilation_ready)
    failed = inspect.getsource(base_app.MainWindow._latex_monitor_failed)
    finished = inspect.getsource(base_app.MainWindow._latex_monitor_worker_finished)

    assert "pipeline.save_state(" in ready
    assert "force_status=True" in ready
    assert "build_latex_monitor_result_presentation(" in ready
    assert "reports/latex/latex_fix_request.md" not in ready
    assert "build_latex_monitor_failure_presentation(" in failed
    assert "latex_monitor_coordinator.finish_scan(" in finished


def test_generic_operation_failure_no_longer_owns_latex_monitor_state() -> None:
    source = inspect.getsource(base_app.MainWindow._operation_failed)

    assert 'purpose == "latex-monitor"' not in source
    assert "latex_monitor_status" not in source


def test_manual_local_compilation_remains_separate_workflow() -> None:
    source = inspect.getsource(base_app.MainWindow.compile_local_tex)

    assert "LatexCompiler" in source
    assert 'activity("latex-compilation")' in source
    assert "latex_monitor_coordinator" not in source
