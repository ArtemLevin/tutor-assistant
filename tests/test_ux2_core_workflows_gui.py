"""GUI contracts for the UX-2 core workflow redesign."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QCheckBox

from tutor_assistant.domain import LessonProcessingMode
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


def test_quick_mode_exposes_automatic_transcript_github_opt_in() -> None:
    page_source = inspect.getsource(app_module.MainWindow._quick_start_page)

    assert "quick_automatic_pipeline" in page_source
    assert "Автоматически транскрибировать и отправить на GitHub" in page_source


def test_detailed_mode_exposes_same_per_lesson_automatic_opt_in() -> None:
    _application()
    lesson_source = inspect.getsource(app_module.MainWindow._lesson_tab)

    assert "detailed_automatic_pipeline" in lesson_source
    assert "Автоматически транскрибировать и отправить на GitHub" in lesson_source

    state = SimpleNamespace(
        _quick_launch_active=False,
        quick_automatic_pipeline=QCheckBox(),
        detailed_automatic_pipeline=QCheckBox(),
    )
    assert (
        app_module.MainWindow._selected_lesson_processing_mode(state)
        == LessonProcessingMode.MANUAL
    )

    state.detailed_automatic_pipeline.setChecked(True)
    assert (
        app_module.MainWindow._selected_lesson_processing_mode(state)
        == LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB
    )

    state._quick_launch_active = True
    assert (
        app_module.MainWindow._selected_lesson_processing_mode(state)
        == LessonProcessingMode.MANUAL
    )
    state.quick_automatic_pipeline.setChecked(True)
    assert (
        app_module.MainWindow._selected_lesson_processing_mode(state)
        == LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB
    )

    app_module.MainWindow._sync_automatic_pipeline_selection(state, False)
    assert not state.quick_automatic_pipeline.isChecked()
    assert not state.detailed_automatic_pipeline.isChecked()


def test_processing_queue_exposes_automatic_publication_runtime() -> None:
    init_source = inspect.getsource(app_module.MainWindow.__init__)
    ready_source = inspect.getsource(
        app_module.MainWindow._background_transcription_ready
    )
    queue_source = inspect.getsource(
        app_module.MainWindow._update_transcription_queue_ui
    )
    retry_source = inspect.getsource(
        app_module.MainWindow._open_automatic_publication_item
    )

    assert "PublicationWorker" in init_source
    assert "PublicationQueueCoordinator" in init_source
    assert "_restore_automatic_publication_jobs" in ready_source
    assert "_pump_publication_queue" in ready_source
    assert '"publication"' in queue_source
    assert '"blocked"' in retry_source
    assert '"retry_required"' in retry_source
    assert '"conflict"' in retry_source
