from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtWidgets import QMessageBox

from .lesson_journal import LessonJournalPage


def install_lesson_journal(window) -> LessonJournalPage:
    page = LessonJournalPage(
        window.crm_store,
        lesson_store=window.pipeline.store,
    )
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
    return page


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
