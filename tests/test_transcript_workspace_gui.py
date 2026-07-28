from __future__ import annotations

import inspect

from PySide6.QtWidgets import QApplication

from tutor_assistant.ui.app import MainWindow
from tutor_assistant.ui.transcript_workspace import (
    NormalizationSettingsDialog,
    TranscriptWorkspace,
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_workspace_hides_result_until_filtering_finishes() -> None:
    _application()
    workspace = TranscriptWorkspace()

    assert workspace.content_tabs.isTabVisible(workspace.result_tab_index) is False
    assert workspace.content_tabs.count() == 3
    assert workspace.primary_action_button.text() == "Запустить фильтрацию"

    workspace.set_result_preview(
        "[П] Решаем неравенство x + 2 > 5.",
        summary="Сохранено 82% · fallback-блоков: 1",
        warnings=["source_fallback:chunk=1"],
        select=True,
    )

    assert workspace.content_tabs.isTabVisible(workspace.result_tab_index) is True
    assert workspace.content_tabs.currentIndex() == workspace.result_tab_index
    assert "x + 2 > 5" in workspace.result_editor.toPlainText()
    assert "1" in workspace.result_warnings.text()


def test_workspace_exposes_one_contextual_primary_action() -> None:
    _application()
    workspace = TranscriptWorkspace()

    workspace.set_primary_action("Отменить", enabled=True, kind="danger")
    workspace.set_progress(
        total=7,
        completed=4,
        title="LLM-фильтрация выполняется",
        detail="Блок 5 из 7 · запросов 6",
    )

    assert workspace.primary_action_button.text() == "Отменить"
    assert workspace.primary_action_button.isEnabled() is True
    assert workspace.progress.isVisible() is True
    assert workspace.progress.maximum() == 7
    assert workspace.progress.value() == 4
    assert "Блок 5" in workspace.process_detail.text()


def test_settings_dialog_limits_retries_and_switches_provider_catalog() -> None:
    _application()
    dialog = NormalizationSettingsDialog(
        provider="ollama",
        model="qwen3:8b",
        retry_requests=0,
    )

    assert dialog.retry_spin.minimum() == 0
    assert dialog.retry_spin.maximum() == 3
    assert dialog.credentials_group.isHidden() is True

    dialog.provider_combo.setCurrentIndex(
        dialog.provider_combo.findData("yandex_ai_studio")
    )

    assert dialog.selected_provider == "yandex_ai_studio"
    assert dialog.model_combo.itemText(0) == "yandexgpt-lite"
    assert dialog.credentials_group.isHidden() is False


def test_main_window_transcript_tab_uses_state_driven_workspace() -> None:
    source = inspect.getsource(MainWindow._transcript_tab)

    assert "TranscriptWorkspace" in source
    assert "transcript_splitter" not in source
    assert "normalization_controls" not in source
    assert "primary_action_button" in source
