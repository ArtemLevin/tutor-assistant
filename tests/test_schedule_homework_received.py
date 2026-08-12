from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tutor_assistant.crm import CrmStore, ScheduleRule, StudentProfile
from tutor_assistant.ui.crm import SchedulePage
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


def test_schedule_slot_homework_checkbox_persists_without_breaking_payment(
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

    row = page._row_for_time(16, 0)
    payment_item = page.grid.item(row, 0)
    homework = controller.checkbox_for(row, 0)
    assert payment_item is not None
    assert homework is not None
    assert payment_item.checkState() == Qt.CheckState.Unchecked
    assert not homework.isChecked()
    assert homework.isEnabled()
    assert "ДЗ получено" in homework.accessibleName()
    assert _occurrence_count(store) == 0

    homework.setChecked(True)
    application.processEvents()
    controller.sync()

    lesson = page.cell_lessons[(row, 0)]
    snapshot = controller.service.snapshot_homework(lesson)
    homework = controller.checkbox_for(row, 0)
    assert homework is not None and homework.isChecked()
    assert snapshot.assigned_at is not None
    assert snapshot.sent_at is not None
    assert snapshot.received_at is not None
    assert _occurrence_count(store) == 1

    # The original schedule payment checkbox remains an independent control.
    page.grid.setCurrentCell(row, 0)
    page.grid.setFocus()
    QTest.keyClick(page.grid, Qt.Key.Key_Space)
    application.processEvents()
    controller.sync()
    assert page.cell_lessons[(row, 0)].paid
    assert controller.checkbox_for(row, 0) is not None
    assert controller.checkbox_for(row, 0).isChecked()
    assert _occurrence_count(store) == 1

    # A recurring lesson on the following week must keep its own homework state.
    page.week_start = date(2026, 8, 10)
    page.refresh()
    application.processEvents()
    controller.sync()
    next_row = page._row_for_time(16, 0)
    next_homework = controller.checkbox_for(next_row, 0)
    assert next_homework is not None
    assert not next_homework.isChecked()
    assert _occurrence_count(store) == 1

    page.week_start = date(2026, 8, 3)
    page.refresh()
    application.processEvents()
    controller.sync()
    restored = controller.checkbox_for(row, 0)
    assert restored is not None and restored.isChecked()

    restored.setChecked(False)
    application.processEvents()
    controller.sync()
    lesson = page.cell_lessons[(row, 0)]
    snapshot = controller.service.snapshot_homework(lesson)
    assert snapshot.sent_at is not None
    assert snapshot.received_at is None

    assert lesson.occurrence_id is not None
    store.update_occurrence(lesson.occurrence_id, status="cancelled")
    page.refresh()
    application.processEvents()
    controller.sync()
    cancelled = controller.checkbox_for(row, 0)
    assert cancelled is not None
    assert not cancelled.isEnabled()
    page.close()
