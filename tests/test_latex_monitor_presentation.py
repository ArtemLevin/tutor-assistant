from __future__ import annotations

from pathlib import Path

from tutor_assistant.ui.latex_monitor_presentation import (
    build_latex_monitor_failure_presentation,
    build_latex_monitor_no_update_presentation,
    build_latex_monitor_result_presentation,
    build_latex_monitor_scanning_presentation,
    build_latex_monitor_toggle_presentation,
)


def test_toggle_presentations_preserve_existing_copy() -> None:
    enabled = build_latex_monitor_toggle_presentation(enabled=True, poll_seconds=30)
    disabled = build_latex_monitor_toggle_presentation(enabled=False, poll_seconds=30)

    assert enabled.monitor_status == "Проверка каждые 30 секунд"
    assert enabled.app_status == "Автомониторинг LaTeX включён"
    assert enabled.tone == "working"
    assert disabled.monitor_status == "Мониторинг выключен"
    assert disabled.app_status == "Автомониторинг LaTeX выключен"


def test_scanning_and_no_update_presentations_are_typed() -> None:
    scanning = build_latex_monitor_scanning_presentation()
    no_update = build_latex_monitor_no_update_presentation()

    assert scanning.monitor_status == "Проверяю удалённые ветки…"
    assert scanning.app_status == "Проверяю ветки занятий…"
    assert scanning.tone == "working"
    assert no_update.monitor_status == "Новых TEX-файлов нет"
    assert no_update.dialog_message is None


def test_success_result_maps_log_preview_status_and_dialog() -> None:
    previews = [Path("page-1.png"), Path("page-2.png")]

    presentation = build_latex_monitor_result_presentation(
        branch="lesson/example",
        success=True,
        attempt=1,
        max_attempts=3,
        warnings=["warning"],
        preview_paths=previews,
    )

    assert presentation.monitor_status == "PDF создан и отправлен в lesson/example"
    assert presentation.app_status == presentation.monitor_status
    assert presentation.tone == "success"
    assert presentation.log_text == "warning"
    assert presentation.preview_paths == tuple(previews)
    assert presentation.replace_previews
    assert presentation.dialog_kind == "information"
    assert presentation.dialog_message == presentation.monitor_status


def test_compile_failure_preserves_fix_request_message() -> None:
    presentation = build_latex_monitor_result_presentation(
        branch="lesson/example",
        success=False,
        attempt=2,
        max_attempts=3,
        errors=["latex error"],
    )

    assert presentation.monitor_status == (
        "Компиляция не удалась, попытка 2/3. "
        "В ветку добавлен reports/latex/latex_fix_request.md"
    )
    assert presentation.tone == "warning"
    assert presentation.log_text == "latex error"


def test_worker_failure_preserves_generic_error_contract_and_truncates_dialog() -> None:
    details = "x" * 4000

    presentation = build_latex_monitor_failure_presentation(details)

    assert presentation.monitor_status == "Ошибка проверки удалённых TEX-файлов"
    assert presentation.app_status == "Ошибка фоновой операции · latex-monitor"
    assert presentation.tone == "error"
    assert presentation.dialog_kind == "critical"
    assert presentation.dialog_message == "x" * 3000
