from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QEvent, QObject, QSignalBlocker
from PySide6.QtWidgets import QMessageBox

from .lesson_journal_responsive import LessonJournalResponsivePage


class LessonJournalCloseGuard(QObject):
    """Protect an unsaved closeout draft when the application window closes."""

    def __init__(self, window, page: LessonJournalResponsivePage) -> None:
        super().__init__(window)
        self.window = window
        self.page = page

    def eventFilter(self, watched, event) -> bool:
        if watched is self.window and event.type() == QEvent.Type.Close:
            if not self.page.confirm_closeout_before_exit():
                event.ignore()
                return True
        return super().eventFilter(watched, event)


def install_lesson_journal(window) -> LessonJournalResponsivePage:
    page = LessonJournalResponsivePage(
        window.crm_store,
        lesson_store=window.pipeline.store,
    )
    _restore_extended_period(page)
    index = window.tabs.addTab(page, "10  Журнал занятий")
    if index != 9:
        raise RuntimeError("Журнал занятий должен использовать page index 9")
    window.lesson_journal_page = page
    page.open_lesson_requested.connect(lambda lesson_id: _open_lesson(window, lesson_id))
    page.open_materials_requested.connect(
        lambda student_id: _open_materials(window, student_id)
    )
    page.show_in_schedule_requested.connect(
        lambda starts_at: _show_in_schedule(window, starts_at)
    )
    window.crm_students_page.changed.connect(page.refresh)
    window.crm_schedule_page.metadata_changed.connect(
        lambda: page.refresh(preserve_context=True)
    )

    close_guard = LessonJournalCloseGuard(window, page)
    window.installEventFilter(close_guard)
    window._lesson_journal_close_guard = close_guard
    return page


def _restore_extended_period(page: LessonJournalResponsivePage) -> None:
    state = getattr(page, "_pending_ux_state", None)
    if not isinstance(state, dict):
        return
    period = str(state.get("period", ""))
    if not period or page.period_filter.currentData() == period:
        return
    index = page.period_filter.findData(period)
    if index < 0:
        return
    blocker = QSignalBlocker(page.period_filter)
    page.period_filter.setCurrentIndex(index)
    del blocker
    page._apply_period_preset()
    page.refresh()


def _open_lesson(window, lesson_id: str) -> None:
    lesson = window.pipeline.store.get(lesson_id)
    if lesson is None:
        QMessageBox.warning(
            window,
            "Журнал занятий",
            "Связанное занятие отсутствует в локальном хранилище",
        )
        return
    window._load_lesson(lesson)


def _open_materials(window, student_id: str) -> None:
    window._open_student_materials(student_id)


def _show_in_schedule(window, starts_at: object) -> None:
    if not isinstance(starts_at, datetime):
        return
    monday = starts_at.date() - timedelta(days=starts_at.weekday())
    page = window.crm_schedule_page
    page.week_start = monday
    page.refresh()
    row = page._row_for_time(starts_at.hour, starts_at.minute)
    column = starts_at.weekday()
    page.grid.setCurrentCell(row, column)
    window._go_to(6)
