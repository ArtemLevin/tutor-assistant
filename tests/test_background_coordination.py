from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from tutor_assistant.config import LatexConfig, RepositoryConfig
from tutor_assistant.content import StudentContentService
from tutor_assistant.domain import Lesson, Student
from tutor_assistant.publisher import GitError
from tutor_assistant.ui.background import (
    BackgroundTaskResult,
    BackgroundTaskState,
    run_latex_monitor_scan,
)


def make_lesson() -> Lesson:
    return Lesson(
        lesson_id="background-coordination",
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 21),
        topic="Фоновая координация",
    )


def test_latex_monitor_busy_is_a_successful_deferred_result(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    with service.activity("content-maintenance", exclusive=True):
        result = run_latex_monitor_scan(
            service,
            RepositoryConfig(students_repo=tmp_path / "students"),
            LatexConfig(),
            [],
            lambda _lesson: tmp_path / "cache",
        )

    assert result.state == BackgroundTaskState.SKIPPED_BUSY
    assert result.blocking_activity == "content-maintenance"
    assert "content-maintenance" in (result.reason or "")


def test_latex_monitor_no_changes_is_distinct_from_busy(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")

    result = run_latex_monitor_scan(
        service,
        RepositoryConfig(students_repo=tmp_path / "students"),
        LatexConfig(),
        [],
        lambda _lesson: tmp_path / "cache",
    )

    assert result.state == BackgroundTaskState.NO_CHANGES
    assert result.reason is None
    assert result.blocking_activity is None


def test_latex_monitor_does_not_hide_real_git_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tutor_assistant.ui import background as background_module

    class FailingRemoteLatexService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def is_ready(self, _lesson: Lesson) -> bool:
            raise GitError("fatal: unable to access remote: connection timed out")

    monkeypatch.setattr(
        background_module,
        "RemoteLatexService",
        FailingRemoteLatexService,
    )
    service = StudentContentService(tmp_path / "data")

    with pytest.raises(GitError, match="connection timed out"):
        run_latex_monitor_scan(
            service,
            RepositoryConfig(students_repo=tmp_path / "students"),
            LatexConfig(),
            [make_lesson()],
            lambda _lesson: tmp_path / "cache",
        )


def test_worker_emits_succeeded_not_failed_for_deferred_result() -> None:
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

    from PySide6.QtWidgets import QApplication

    from tutor_assistant.ui.app import Worker

    application = QApplication.instance() or QApplication([])
    result = BackgroundTaskResult[object].skipped_busy(
        "Хранилище занято: content-maintenance",
        blocking_activity="content-maintenance",
    )
    succeeded: list[object] = []
    failed: list[str] = []
    worker = Worker(lambda: result)
    worker.succeeded.connect(succeeded.append)
    worker.failed.connect(failed.append)

    worker.start()
    assert worker.wait(5000)
    application.processEvents()

    assert succeeded == [result]
    assert failed == []


def test_busy_result_does_not_open_critical_dialog(monkeypatch) -> None:
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

    from PySide6.QtWidgets import QMessageBox

    from tutor_assistant.ui.concurrent_app import MainWindow

    class Label:
        def __init__(self) -> None:
            self.text = ""

        def setText(self, text: str) -> None:
            self.text = text

    critical_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *args, **_kwargs: critical_calls.append(args),
    )
    statuses: list[tuple[str, str]] = []
    window = SimpleNamespace(
        latex_monitor_status=Label(),
        _latex_monitor_deferred=False,
        _latex_monitor_deferred_manual=False,
        _set_status=lambda message, tone="success": statuses.append((message, tone)),
        _operation_failed=lambda *_args: pytest.fail("busy must not be reported as failure"),
    )
    result = BackgroundTaskResult[object].skipped_busy(
        "Хранилище занято: content-maintenance",
        blocking_activity="content-maintenance",
        manually_requested=True,
    )

    MainWindow._remote_compilation_ready(window, result)

    assert window._latex_monitor_deferred
    assert window._latex_monitor_deferred_manual
    assert window.latex_monitor_status.text == "Проверка отложена: обслуживается архив"
    assert statuses[-1][1] == "warning"
    assert critical_calls == []


@pytest.mark.parametrize(
    ("manually_requested", "auto_enabled", "expected"),
    [
        (False, True, True),
        (True, False, True),
        (False, False, False),
    ],
)
def test_deferred_monitor_resume_policy(
    monkeypatch,
    manually_requested: bool,
    auto_enabled: bool,
    expected: bool,
) -> None:
    pytest.importorskip("PySide6.QtCore", exc_type=ImportError)

    from tutor_assistant.ui import concurrent_app as app_module

    scheduled: list[tuple[int, object]] = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay: int, callback) -> None:
            scheduled.append((delay, callback))

    class CheckBox:
        def isChecked(self) -> bool:
            return auto_enabled

    calls: list[bool] = []
    window = SimpleNamespace(
        _latex_monitor_deferred=True,
        _latex_monitor_deferred_manual=manually_requested,
        _shutdown_requested=False,
        auto_latex=CheckBox(),
        workers=[],
        scan_remote_latex=lambda *, manually_requested=False: calls.append(
            manually_requested
        ),
    )
    monkeypatch.setattr(app_module, "QTimer", FakeTimer)

    app_module.MainWindow._resume_deferred_latex_monitor(window)

    assert bool(scheduled) is expected
    if expected:
        delay, callback = scheduled[0]
        assert delay == 500
        callback()
        assert calls == [manually_requested]
        assert not window._latex_monitor_deferred
        assert not window._latex_monitor_deferred_manual
    else:
        assert calls == []
        assert window._latex_monitor_deferred
