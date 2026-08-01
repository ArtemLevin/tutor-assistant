"""GUI contracts for the UX-2 core workflow redesign."""

from __future__ import annotations

import inspect

from PySide6.QtWidgets import QApplication

from tutor_assistant.ui import app as app_module
from tutor_assistant.ui.content_import import ImportLessonDialog
from tutor_assistant.ui.crm import SchedulePage, StudentsPage
from tutor_assistant.ui.normalization import ContentFilterReviewDialog
from tutor_assistant.ui.transcript_workspace import TranscriptWorkspace

_APPLICATION: QApplication | None = None


def _application() -> QApplication:
    global _APPLICATION
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        _APPLICATION = existing
    elif _APPLICATION is None:
        _APPLICATION = QApplication([])
    return _APPLICATION


def test_quick_mode_exposes_profile_subject_and_readiness_text() -> None:
    source = inspect.getsource(app_module.MainWindow._quick_start_page)
    refresh_source = inspect.getsource(app_module.MainWindow._refresh_quick_readiness)

    assert "quick_profile_text" in source
    assert "quick_subject_text" in source
    assert "quick_readiness_text" in source
    assert "Профиль:" in refresh_source
    assert "Предмет:" in refresh_source
    assert "Готово к старту" in refresh_source


def test_transcript_workspace_has_one_explicit_final_action() -> None:
    _application()
    workspace = TranscriptWorkspace()

    assert workspace.approve_button.text() == "Подтвердить и перейти к публикации"
    assert workspace.review_result_button.isHidden()
    assert not hasattr(workspace, "open_review_action")

    workspace.set_review_action(visible=True, enabled=True)
    workspace.set_primary_action("Запустить фильтрацию", enabled=False, visible=False)

    assert not workspace.review_result_button.isHidden()
    assert workspace.review_result_button.isEnabled()
    assert workspace.primary_action_button.isHidden()


def test_idle_progress_bars_are_hidden() -> None:
    _application()
    workspace = TranscriptWorkspace()
    assert workspace.progress.isHidden()

    workspace.set_progress(total=4, completed=1, title="Выполняется", detail="Блок 1")
    assert not workspace.progress.isHidden()
    workspace.set_process_state("Готово", "Операция завершена", tone="success")
    assert workspace.progress.isHidden()
    assert workspace.progress.value() == 0

    dialog = ImportLessonDialog([])
    dialog.set_running()
    assert not dialog.progress.isHidden()
    dialog.show_error("Ошибка импорта")
    assert dialog.progress.isHidden()


def test_required_review_and_explicit_actions_are_first_class_controls() -> None:
    app_source = inspect.getsource(app_module.MainWindow)
    students_source = inspect.getsource(StudentsPage._build)
    schedule_source = inspect.getsource(SchedulePage._build)
    review_source = inspect.getsource(ContentFilterReviewDialog.__init__)

    assert "processing_open_button" in app_source
    assert "open_pdf_preview_button" in app_source
    assert "review_result_button.clicked.connect" in app_source
    assert "edit_guardian_button" in students_source
    assert "open_selected_button" in schedule_source
    assert "Применить и перейти к публикации" in review_source
