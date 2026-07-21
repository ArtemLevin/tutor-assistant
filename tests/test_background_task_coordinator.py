from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication  # noqa: E402

from tutor_assistant.content import ContentBusyError, StudentContentService  # noqa: E402
from tutor_assistant.ui.background import (  # noqa: E402
    BackgroundTaskPhase,
    BackgroundTaskPurpose,
    BackgroundTaskSpec,
    BusyPolicy,
)
from tutor_assistant.ui.background_tasks import BackgroundTaskCoordinator  # noqa: E402


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def wait_until(
    application: QApplication,
    predicate,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    application.processEvents()
    assert predicate()


def test_success_releases_lease_and_removes_worker(
    tmp_path: Path,
    application: QApplication,
) -> None:
    service = StudentContentService(tmp_path / "data")
    registry = []
    coordinator = BackgroundTaskCoordinator(service, registry)
    results = []

    assert coordinator.submit(
        BackgroundTaskSpec(
            purpose=BackgroundTaskPurpose.LATEX_MONITOR,
            operation=lambda: "done",
            activity="latex-monitor",
        ),
        on_success=results.append,
    )

    wait_until(application, lambda: coordinator.running_count() == 0)

    assert results[0].payload == "done"
    assert coordinator.phase(BackgroundTaskPurpose.LATEX_MONITOR) == (
        BackgroundTaskPhase.COMPLETED
    )
    assert service.active_activities() == []
    assert registry == []


def test_real_exception_uses_failure_channel_and_releases_lease(
    tmp_path: Path,
    application: QApplication,
) -> None:
    service = StudentContentService(tmp_path / "data")
    coordinator = BackgroundTaskCoordinator(service)
    failures: list[str] = []

    def fail() -> None:
        raise RuntimeError("network exploded")

    assert coordinator.submit(
        BackgroundTaskSpec(
            purpose=BackgroundTaskPurpose.LATEX_MONITOR,
            operation=fail,
            activity="latex-monitor",
        ),
        on_failure=failures.append,
    )

    wait_until(application, lambda: coordinator.running_count() == 0)

    assert failures and "network exploded" in failures[0]
    assert coordinator.phase(BackgroundTaskPurpose.LATEX_MONITOR) == (
        BackgroundTaskPhase.FAILED
    )
    assert service.active_activities() == []


@pytest.mark.parametrize(
    ("policy", "expected_phase", "deferred"),
    [
        (BusyPolicy.FAIL, BackgroundTaskPhase.SKIPPED, False),
        (BusyPolicy.SKIP, BackgroundTaskPhase.SKIPPED, False),
        (BusyPolicy.DEFER, BackgroundTaskPhase.DEFERRED, True),
    ],
)
def test_busy_policies_do_not_create_failed_worker(
    tmp_path: Path,
    policy: BusyPolicy,
    expected_phase: BackgroundTaskPhase,
    deferred: bool,
) -> None:
    blocker_service = StudentContentService(tmp_path / policy.value / "data")
    service = StudentContentService(tmp_path / policy.value / "data")
    coordinator = BackgroundTaskCoordinator(service)
    busy_results = []
    failures: list[str] = []
    blocker = blocker_service.acquire_activity("content-maintenance", exclusive=True)
    try:
        started = coordinator.submit(
            BackgroundTaskSpec(
                purpose=BackgroundTaskPurpose.LATEX_MONITOR,
                operation=lambda: "not-run",
                activity="latex-monitor",
                busy_policy=policy,
            ),
            on_busy=busy_results.append,
            on_failure=failures.append,
        )
    finally:
        blocker.release()

    assert not started
    assert busy_results
    assert failures == []
    assert coordinator.phase(BackgroundTaskPurpose.LATEX_MONITOR) == expected_phase
    assert coordinator.has_pending() is deferred


def test_local_blocker_completion_retries_deferred_task_once(
    tmp_path: Path,
    application: QApplication,
) -> None:
    service = StudentContentService(tmp_path / "data")
    coordinator = BackgroundTaskCoordinator(service)
    calls: list[str] = []
    phases: list[str] = []
    coordinator.state_changed.connect(lambda _purpose, phase: phases.append(phase))
    blocker = service.acquire_activity("content-maintenance", exclusive=True)

    assert not coordinator.submit(
        BackgroundTaskSpec(
            purpose=BackgroundTaskPurpose.LATEX_MONITOR,
            operation=lambda: calls.append("run") or "done",
            activity="latex-monitor",
            busy_policy=BusyPolicy.DEFER,
            defer_delay_ms=1,
        )
    )
    blocker.release()
    coordinator.resume_deferred(released_activity="content-maintenance")
    coordinator.resume_deferred(released_activity="content-maintenance")

    wait_until(application, lambda: coordinator.running_count() == 0 and bool(calls))

    assert calls == ["run"]
    assert BackgroundTaskPhase.DEFERRED.value in phases
    assert phases.count(BackgroundTaskPhase.RUNNING.value) == 1
    assert coordinator.phase(BackgroundTaskPurpose.LATEX_MONITOR) == (
        BackgroundTaskPhase.COMPLETED
    )


def test_external_blocker_waits_for_next_submit_instead_of_tight_loop(
    tmp_path: Path,
    application: QApplication,
) -> None:
    first = StudentContentService(tmp_path / "data")
    second = StudentContentService(tmp_path / "data")
    coordinator = BackgroundTaskCoordinator(second)
    calls: list[str] = []
    spec = BackgroundTaskSpec(
        purpose=BackgroundTaskPurpose.LATEX_MONITOR,
        operation=lambda: calls.append("run") or "done",
        activity="latex-monitor",
        busy_policy=BusyPolicy.DEFER,
        defer_delay_ms=1,
    )
    blocker = first.acquire_activity("content-maintenance", exclusive=True)

    assert not coordinator.submit(spec)
    coordinator.resume_deferred(released_activity="content-maintenance")
    application.processEvents()
    time.sleep(0.05)
    application.processEvents()
    assert calls == []
    assert coordinator.phase(BackgroundTaskPurpose.LATEX_MONITOR) == (
        BackgroundTaskPhase.DEFERRED
    )

    blocker.release()
    assert coordinator.submit(spec)
    wait_until(application, lambda: coordinator.running_count() == 0)
    assert calls == ["run"]


def test_duplicate_purpose_creates_one_worker(
    tmp_path: Path,
    application: QApplication,
) -> None:
    service = StudentContentService(tmp_path / "data")
    coordinator = BackgroundTaskCoordinator(service)
    release = threading.Event()
    started = threading.Event()
    busy = []

    def operation() -> str:
        started.set()
        release.wait(3)
        return "done"

    assert coordinator.submit(
        BackgroundTaskSpec(
            purpose=BackgroundTaskPurpose.LATEX_MONITOR,
            operation=operation,
        )
    )
    assert started.wait(2)
    assert not coordinator.submit(
        BackgroundTaskSpec(
            purpose=BackgroundTaskPurpose.LATEX_MONITOR,
            operation=lambda: "duplicate",
            manually_requested=True,
        ),
        on_busy=busy.append,
    )
    assert coordinator.running_count(BackgroundTaskPurpose.LATEX_MONITOR) == 1
    assert busy and busy[0].manually_requested

    release.set()
    wait_until(application, lambda: coordinator.running_count() == 0)


def test_parallel_content_tasks_preserve_existing_request_model(
    tmp_path: Path,
    application: QApplication,
) -> None:
    service = StudentContentService(tmp_path / "data")
    coordinator = BackgroundTaskCoordinator(service)
    release = threading.Event()
    started = threading.Barrier(3)

    def operation() -> str:
        started.wait(timeout=3)
        release.wait(3)
        return "done"

    spec = lambda: BackgroundTaskSpec(
        purpose=BackgroundTaskPurpose.CONTENT_BROWSER,
        operation=operation,
        allow_parallel=True,
    )
    assert coordinator.submit(spec())
    assert coordinator.submit(spec())
    started.wait(timeout=3)
    assert coordinator.running_count(BackgroundTaskPurpose.CONTENT_BROWSER) == 2

    release.set()
    wait_until(application, lambda: coordinator.running_count() == 0)


def test_content_busy_raised_inside_operation_uses_busy_policy(
    tmp_path: Path,
    application: QApplication,
) -> None:
    service = StudentContentService(tmp_path / "data")
    coordinator = BackgroundTaskCoordinator(service)
    busy = []
    failures: list[str] = []

    def operation() -> None:
        raise ContentBusyError("Хранилище занято: content-delete")

    assert coordinator.submit(
        BackgroundTaskSpec(
            purpose=BackgroundTaskPurpose.CONTENT_BROWSER,
            operation=operation,
            busy_policy=BusyPolicy.FAIL,
        ),
        on_busy=busy.append,
        on_failure=failures.append,
    )
    wait_until(application, lambda: coordinator.running_count() == 0)

    assert busy and "content-delete" in (busy[0].reason or "")
    assert failures == []
    assert coordinator.phase(BackgroundTaskPurpose.CONTENT_BROWSER) == (
        BackgroundTaskPhase.SKIPPED
    )


def test_manual_request_upgrades_existing_deferred_task(
    tmp_path: Path,
) -> None:
    first = StudentContentService(tmp_path / "data")
    second = StudentContentService(tmp_path / "data")
    coordinator = BackgroundTaskCoordinator(second)
    busy = []
    blocker = first.acquire_activity("content-maintenance", exclusive=True)
    try:
        assert not coordinator.submit(
            BackgroundTaskSpec(
                purpose=BackgroundTaskPurpose.LATEX_MONITOR,
                operation=lambda: None,
                activity="latex-monitor",
                busy_policy=BusyPolicy.DEFER,
            ),
            on_busy=busy.append,
        )
        assert not coordinator.submit(
            BackgroundTaskSpec(
                purpose=BackgroundTaskPurpose.LATEX_MONITOR,
                operation=lambda: None,
                activity="latex-monitor",
                busy_policy=BusyPolicy.DEFER,
                manually_requested=True,
            ),
            on_busy=busy.append,
        )
    finally:
        blocker.release()

    assert busy[-1].manually_requested


def test_shutdown_cancels_deferred_and_rejects_new_tasks(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    coordinator = BackgroundTaskCoordinator(service)
    blocker = service.acquire_activity("content-maintenance", exclusive=True)
    try:
        assert not coordinator.submit(
            BackgroundTaskSpec(
                purpose=BackgroundTaskPurpose.LATEX_MONITOR,
                operation=lambda: None,
                activity="latex-monitor",
                busy_policy=BusyPolicy.DEFER,
            )
        )
        assert coordinator.has_pending()
        coordinator.begin_shutdown()
        assert not coordinator.has_pending()
        assert not coordinator.submit(
            BackgroundTaskSpec(
                purpose=BackgroundTaskPurpose.CONTENT_BROWSER,
                operation=lambda: None,
            )
        )
    finally:
        blocker.release()
