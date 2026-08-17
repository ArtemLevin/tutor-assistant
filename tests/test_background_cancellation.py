from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication  # noqa: E402

from tutor_assistant.content import StudentContentService  # noqa: E402
from tutor_assistant.ui.background import (  # noqa: E402
    BackgroundTaskPhase,
    BackgroundTaskPurpose,
    BackgroundTaskSpec,
)
from tutor_assistant.ui.background_tasks import BackgroundTaskCoordinator  # noqa: E402


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(application: QApplication, predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    application.processEvents()
    assert predicate()


def test_cooperative_cancel_stops_operation_without_failure_callback(
    tmp_path: Path,
    application: QApplication,
) -> None:
    service = StudentContentService(tmp_path / "data")
    coordinator = BackgroundTaskCoordinator(service)
    started = threading.Event()
    callbacks: list[str] = []

    def operation(cancellation) -> str:
        started.set()
        while True:
            cancellation.raise_if_cancelled()
            time.sleep(0.005)

    assert coordinator.submit(
        BackgroundTaskSpec(
            purpose=BackgroundTaskPurpose.CONTENT_BROWSER,
            operation=operation,
            accepts_cancellation=True,
        ),
        on_success=lambda _result: callbacks.append("success"),
        on_failure=lambda _details: callbacks.append("failure"),
        on_finished=lambda: callbacks.append("finished"),
    )
    assert started.wait(2)

    assert coordinator.cancel(BackgroundTaskPurpose.CONTENT_BROWSER) == 1
    wait_until(application, lambda: coordinator.running_count() == 0)

    assert callbacks == []
    assert coordinator.phase(BackgroundTaskPurpose.CONTENT_BROWSER) == BackgroundTaskPhase.CANCELLED
    assert service.active_activities() == []


def test_shutdown_suppresses_late_non_cooperative_callbacks(
    tmp_path: Path,
    application: QApplication,
) -> None:
    service = StudentContentService(tmp_path / "data")
    registry = []
    coordinator = BackgroundTaskCoordinator(service, registry)
    started = threading.Event()
    release = threading.Event()
    callbacks: list[str] = []

    def operation() -> str:
        started.set()
        release.wait(3)
        return "late-result"

    assert coordinator.submit(
        BackgroundTaskSpec(
            purpose=BackgroundTaskPurpose.LATEX_MONITOR,
            operation=operation,
            activity="latex-monitor",
        ),
        on_success=lambda _result: callbacks.append("success"),
        on_failure=lambda _details: callbacks.append("failure"),
        on_finished=lambda: callbacks.append("finished"),
    )
    assert started.wait(2)

    coordinator.begin_shutdown()
    assert coordinator.phase(BackgroundTaskPurpose.LATEX_MONITOR) == BackgroundTaskPhase.CANCELLING
    release.set()
    wait_until(application, lambda: coordinator.running_count() == 0)

    assert callbacks == []
    assert coordinator.phase(BackgroundTaskPurpose.LATEX_MONITOR) == BackgroundTaskPhase.CANCELLED
    assert registry == []
    assert service.active_activities() == []
