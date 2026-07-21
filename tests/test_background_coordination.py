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
    scan_remote_latex,
)


def make_lesson() -> Lesson:
    return Lesson(
        lesson_id="background-coordination",
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 21),
        topic="Фоновая координация",
    )


def test_compatibility_latex_monitor_busy_returns_structured_result(
    tmp_path: Path,
) -> None:
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
    assert [item.activity for item in result.blockers] == ["content-maintenance"]
    assert "content-maintenance" in (result.reason or "")


def test_compatibility_latex_monitor_no_changes_is_distinct_from_busy(
    tmp_path: Path,
) -> None:
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
    assert result.blockers == ()


def test_pure_latex_scan_does_not_hide_real_git_errors(
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

    with pytest.raises(GitError, match="connection timed out"):
        scan_remote_latex(
            RepositoryConfig(students_repo=tmp_path / "students"),
            LatexConfig(),
            [make_lesson()],
            lambda _lesson: tmp_path / "cache",
        )


def test_busy_gui_state_does_not_open_critical_dialog(monkeypatch) -> None:
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
        _set_status=lambda message, tone="success": statuses.append((message, tone)),
    )
    result = BackgroundTaskResult[object].skipped_busy(
        "Хранилище занято: content-maintenance",
        blocking_activity="content-maintenance",
        manually_requested=True,
    )

    MainWindow._remote_monitor_busy(window, result)

    assert window.latex_monitor_status.text == "Проверка отложена: обслуживается архив"
    assert statuses[-1][1] == "warning"
    assert critical_calls == []
