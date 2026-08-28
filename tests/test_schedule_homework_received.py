from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from tutor_assistant.crm import CrmStore, ScheduleRule, StudentProfile
from tutor_assistant.ui.crm import ScheduleDialog, SchedulePage
from tutor_assistant.ui.schedule_homework import ScheduleHomeworkReceivedController


class TestCodec:
    def encrypt(self, value: str | None) -> str | None:
        return value

    def decrypt(self, value: str | None) -> str | None:
        return value


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _store(tmp_path: Path) -> CrmStore:
    store = CrmStore(tmp_path / "schedule-homework.sqlite3", TestCodec())
    store.save_student(StudentProfile(id="student", full_name="Ученик"), [])
    store.save_schedule_rule(
        ScheduleRule(
            student_id="student",
            weekday=0,
            start_minute=16 * 60,
            duration_minutes=90,
            subject="mathematics",
            topic="Получение ДЗ",
            valid_from=date(2026, 8, 1),
            rate_cents=300_000,
        )
    )
    return store


def _occurrence_count(store: CrmStore) -> int:
    with sqlite3.connect(store.path) as db:
        return int(db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0])


def test_schedule_dialog_homework_checkbox_persists_without_grid_overlay(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path)
    page = SchedulePage(store)
    page.week_start = date(2026, 8, 3)
    page.refresh()
    page.show()
    application.processEvents()

    row = page._row_for_time(16, 0)
    lesson = page.cell_lessons[(row, 0)]
    item = page.grid.item(row, 0)
    assert item is not None and item.text() == "Ученик"
    assert page.grid.cellWidget(row, 0) is None
    assert _occurrence_count(store) == 0

    dialog = ScheduleDialog(
        store,
        lesson.starts_at.date(),
        lesson.starts_at.hour,
        lesson.starts_at.minute,
        lesson,
    )
    assert not dialog.homework_received.isChecked()
    dialog.homework_received.setChecked(True)
    application.processEvents()

    snapshot = dialog._homework_service.snapshot_homework(lesson)
    assert dialog.metadata_changed
    assert snapshot.assigned_at is not None
    assert snapshot.sent_at is not None
    assert snapshot.received_at is not None
    assert _occurrence_count(store) == 1
    dialog.close()

    page.week_start = date(2026, 8, 10)
    page.refresh()
    next_row = page._row_for_time(16, 0)
    next_lesson = page.cell_lessons[(next_row, 0)]
    next_dialog = ScheduleDialog(
        store,
        next_lesson.starts_at.date(),
        next_lesson.starts_at.hour,
        next_lesson.starts_at.minute,
        next_lesson,
    )
    assert not next_dialog.homework_received.isChecked()
    next_dialog.close()
    assert _occurrence_count(store) == 1

    page.week_start = date(2026, 8, 3)
    page.refresh()
    restored_lesson = page.cell_lessons[(row, 0)]
    restored_dialog = ScheduleDialog(
        store,
        restored_lesson.starts_at.date(),
        restored_lesson.starts_at.hour,
        restored_lesson.starts_at.minute,
        restored_lesson,
    )
    assert restored_dialog.homework_received.isChecked()
    restored_dialog.homework_received.setChecked(False)
    application.processEvents()
    snapshot = restored_dialog._homework_service.snapshot_homework(restored_lesson)
    assert snapshot.sent_at is not None
    assert snapshot.received_at is None
    restored_dialog.close()

    assert restored_lesson.occurrence_id is not None
    store.update_occurrence(restored_lesson.occurrence_id, status="cancelled")
    page.refresh()

    assert page.grid.item(row, 0) is None
    assert (row, 0) not in page.cell_lessons
    hidden = page.cancelled_cell_lessons[(row, 0)]
    hidden_dialog = ScheduleDialog(
        store,
        hidden.starts_at.date(),
        hidden.starts_at.hour,
        hidden.starts_at.minute,
        hidden,
    )
    snapshot = hidden_dialog._homework_service.snapshot_homework(hidden)
    assert snapshot.sent_at is not None
    assert snapshot.received_at is None
    hidden_dialog.close()
    page.close()


def test_schedule_controller_ignores_queued_sync_after_grid_destroy(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path)
    page = SchedulePage(store)
    page.week_start = date(2026, 8, 3)
    page.refresh()
    controller = ScheduleHomeworkReceivedController(page)
    page.show()
    application.processEvents()
    controller.sync()

    controller.schedule_sync()
    grid = page.grid
    grid.deleteLater()
    QApplication.sendPostedEvents(grid, QEvent.Type.DeferredDelete)
    application.processEvents()

    assert not controller._active
    assert controller.checkbox_for(0, 0) is None

    controller.schedule_sync()
    controller.sync()
    controller.eventFilter(page, QEvent(QEvent.Type.Resize))
    application.processEvents()
    page.close()
