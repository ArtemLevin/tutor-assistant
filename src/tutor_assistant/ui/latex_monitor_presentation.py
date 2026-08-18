from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class LatexMonitorPresentation:
    monitor_status: str
    app_status: str
    tone: str = "success"
    log_text: str | None = None
    preview_paths: tuple[Path, ...] = ()
    replace_previews: bool = False
    dialog_title: str | None = None
    dialog_message: str | None = None
    dialog_kind: str | None = None


def build_latex_monitor_toggle_presentation(
    *,
    enabled: bool,
    poll_seconds: int,
) -> LatexMonitorPresentation:
    if enabled:
        return LatexMonitorPresentation(
            monitor_status=f"Проверка каждые {poll_seconds} секунд",
            app_status="Автомониторинг LaTeX включён",
            tone="working",
        )
    return LatexMonitorPresentation(
        monitor_status="Мониторинг выключен",
        app_status="Автомониторинг LaTeX выключен",
    )


def build_latex_monitor_scanning_presentation() -> LatexMonitorPresentation:
    return LatexMonitorPresentation(
        monitor_status="Проверяю удалённые ветки…",
        app_status="Проверяю ветки занятий…",
        tone="working",
    )


def build_latex_monitor_no_update_presentation() -> LatexMonitorPresentation:
    return LatexMonitorPresentation(
        monitor_status="Новых TEX-файлов нет",
        app_status="Новых TEX-файлов нет",
    )


def build_latex_monitor_result_presentation(
    *,
    branch: str,
    success: bool,
    attempt: int,
    max_attempts: int,
    errors: Iterable[str] = (),
    warnings: Iterable[str] = (),
    preview_paths: Iterable[Path] = (),
) -> LatexMonitorPresentation:
    if success:
        message = f"PDF создан и отправлен в {branch}"
        tone = "success"
    else:
        message = (
            f"Компиляция не удалась, попытка {attempt}/{max_attempts}. "
            "В ветку добавлен reports/latex/latex_fix_request.md"
        )
        tone = "warning"
    diagnostics = [*errors, *warnings]
    return LatexMonitorPresentation(
        monitor_status=message,
        app_status=message,
        tone=tone,
        log_text="\n".join(diagnostics) or message,
        preview_paths=tuple(preview_paths),
        replace_previews=True,
        dialog_title="Автоматическая компиляция",
        dialog_message=message,
        dialog_kind="information",
    )


def build_latex_monitor_failure_presentation(details: str) -> LatexMonitorPresentation:
    return LatexMonitorPresentation(
        monitor_status="Ошибка проверки удалённых TEX-файлов",
        app_status="Ошибка фоновой операции · latex-monitor",
        tone="error",
        dialog_title="Ошибка фоновой операции",
        dialog_message=details[-3000:],
        dialog_kind="critical",
    )
