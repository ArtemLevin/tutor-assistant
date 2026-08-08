from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from tutor_assistant.content import StudentContentService
from tutor_assistant.crm import CrmStore, ScheduledLesson, ScheduleRule, StudentProfile
from tutor_assistant.domain import Lesson, Student
from tutor_assistant.playback import PlaybackController
from tutor_assistant.ui.crm import SchedulePage, StudentsPage
from tutor_assistant.ui.student_content import StudentContentPage


class TestCodec:
    def encrypt(self, value: str | None) -> str | None:
        return value

    def decrypt(self, value: str | None) -> str | None:
        return value


class FakePlaybackBackend(QObject):
    position_changed = Signal(int)
    duration_changed = Signal(int)
    playing_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.position = 0
        self.playing = False

    def load(self, _path: Path) -> None:
        self.position = 0
        self.position_changed.emit(0)

    def play(self) -> None:
        self.playing = True
        self.playing_changed.emit(True)

    def pause(self) -> None:
        self.playing = False
        self.playing_changed.emit(False)

    def stop(self) -> None:
        self.pause()

    def set_position(self, position_ms: int) -> None:
        self.position = position_ms
        self.position_changed.emit(position_ms)

    def position_ms(self) -> int:
        return self.position

    def set_rate(self, _rate: float) -> None:
        return

    def is_playing(self) -> bool:
        return self.playing


class FakeScheduler:
    def schedule(self, _delay_ms: int, _callback) -> None:
        return

    def cancel(self) -> None:
        return


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_student_card_dirty_state_and_field_hierarchy(
    tmp_path: Path,
    application: QApplication,
    monkeypatch,
) -> None:
    store = CrmStore(tmp_path / "crm.sqlite3", TestCodec())
    store.save_student(StudentProfile(id="anna", full_name="Анна"), [])
    store.save_student(StudentProfile(id="boris", full_name="Борис"), [])
    page = StudentsPage(store)
    page.show()
    page.table.selectRow(0)
    application.processEvents()

    assert page.technical_panel.isHidden()
    assert page.dirty_label.text() == "Все изменения сохранены"
    assert page.dirty_label.accessibleName() == "Состояние сохранения карточки ученика"
    assert page.technical_toggle.accessibleName() == "Показать технические параметры ученика"
    page.full_name.setText("Анна Петрова")
    page.full_name.textEdited.emit("Анна Петрова")
    assert page._dirty
    assert page.save_button.isEnabled()
    assert page.dirty_label.text() == "Есть несохранённые изменения"

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.Cancel,
    )
    page.table.selectRow(1)
    application.processEvents()
    assert page.current_id == "anna"
    assert page._dirty

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.Discard,
    )
    page.table.selectRow(1)
    application.processEvents()
    assert page.current_id == "boris"
    assert not page._dirty

    page.technical_toggle.setChecked(True)
    assert not page.technical_panel.isHidden()
    assert page.technical_toggle.accessibleName() == "Скрыть технические параметры ученика"
    page.close()


def test_materials_are_embedded_in_split_view_with_maintenance_menu(
    tmp_path: Path,
    application: QApplication,
) -> None:
    service = StudentContentService(tmp_path / "data")
    student = Student(id="student", full_name="Ученик")
    service.create_lesson(
        Lesson(
            lesson_id="ux3-material",
            student=student,
            subject="mathematics",
            lesson_date=date(2026, 8, 1),
            topic="Split view",
        )
    )
    backend = FakePlaybackBackend()
    controller = PlaybackController(backend, FakeScheduler(), lambda: True)

    def run_background(callable_, succeeded, failed) -> None:
        try:
            succeeded(callable_())
        except Exception as exc:
            failed(str(exc))

    page = StudentContentPage(service, [student], run_background, controller, backend)
    page.ensure_loaded()
    page.show()
    application.processEvents()

    assert page.content_splitter.count() == 2
    assert page.content_splitter.accessibleName() == "Список и содержимое материалов"
    assert page.maintenance_button.accessibleName() == "Меню обслуживания архива материалов"
    assert page.maintenance_button.menu() is page.maintenance_menu
    assert [action.text() for action in page.maintenance_menu.actions() if action.text()] == [
        "Корзина",
        "Диагностика архива",
        "Проверить и восстановить",
    ]
    assert page.trash_action.shortcut() == QKeySequence("Ctrl+Shift+Delete")
    assert page.health_action.shortcut() == QKeySequence("Ctrl+Shift+D")
    assert page.sync_action.shortcut() == QKeySequence("Ctrl+Shift+R")
    page.table.selectRow(0)
    application.processEvents()
    assert page.details_title.text() == "Split view"
    assert page.metadata["topic"].text() == "Split view"
    page.close()


def test_schedule_uses_half_hour_rows_and_duration_spans(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "schedule.sqlite3", TestCodec())
    store.save_student(StudentProfile(id="student", full_name="Ученик"), [])
    week_start = date(2026, 7, 27)
    store.save_one_off(
        ScheduledLesson(
            student_id="student",
            student_name="Ученик",
            starts_at=datetime(2026, 7, 27, 16, 30),
            duration_minutes=90,
            subject="mathematics",
            topic="Полуторачасовое занятие",
        )
    )
    page = SchedulePage(store)
    page.week_start = week_start
    page.refresh()
    page.show()
    application.processEvents()

    assert page.grid.rowCount() == 32
    assert page.grid.verticalHeaderItem(0).text() == "08:00"
    assert page.grid.verticalHeaderItem(31).text() == "23:30"
    row = page._row_for_time(16, 30)
    assert page.grid.verticalHeaderItem(row).text() == "16:30"
    assert page.grid.rowSpan(row, 0) == 3
    lesson = page.cell_lessons[(row, 0)]
    assert page.cell_lessons[(row + 1, 0)] is lesson
    assert page.cell_lessons[(row + 2, 0)] is lesson
    page.grid.setCurrentCell(row + 1, 0)
    assert page.open_selected_button.text() == "Открыть занятие"
    page.close()


def test_schedule_payment_checkbox_persists_and_isolates_recurring_dates(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "schedule-payment.sqlite3", TestCodec())
    store.save_student(StudentProfile(id="student", full_name="Ученик"), [])
    store.save_schedule_rule(
        ScheduleRule(
            student_id="student",
            weekday=0,
            start_minute=16 * 60,
            duration_minutes=90,
            subject="mathematics",
            topic="Оплата занятия",
            valid_from=date(2026, 8, 1),
            rate_cents=300_000,
        )
    )
    page = SchedulePage(store)
    page.week_start = date(2026, 8, 3)

    with sqlite3.connect(store.path) as db:
        before_refresh = db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0]
    page.refresh()
    with sqlite3.connect(store.path) as db:
        after_refresh = db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0]
    assert before_refresh == after_refresh == 0

    page.show()
    application.processEvents()
    row = page._row_for_time(16, 0)
    item = page.grid.item(row, 0)
    assert item is not None
    assert item.checkState() == Qt.CheckState.Unchecked
    assert "Не оплачено" in item.text()
    assert item.background().color().name().upper() == "#FFF0F0"
    assert page.grid.rowSpan(row, 0) == 3

    page.grid.setCurrentCell(row, 0)
    QTest.keyClick(page.grid, Qt.Key.Key_Space)
    application.processEvents()

    paid_item = page.grid.item(row, 0)
    assert paid_item is not None
    assert paid_item.checkState() == Qt.CheckState.Checked
    assert "Оплачено" in paid_item.text()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0] == 1
        assert db.execute("SELECT paid FROM crm_lesson_occurrences").fetchone()[0] == 1

    page.week_start = date(2026, 8, 10)
    page.refresh()
    next_row = page._row_for_time(16, 0)
    next_item = page.grid.item(next_row, 0)
    assert next_item is not None
    assert next_item.checkState() == Qt.CheckState.Unchecked

    page.week_start = date(2026, 8, 3)
    page.refresh()
    restored_item = page.grid.item(row, 0)
    assert restored_item is not None
    assert restored_item.checkState() == Qt.CheckState.Checked

    paid_lesson = page.cell_lessons[(row, 0)]
    assert paid_lesson.occurrence_id is not None
    store.update_occurrence(paid_lesson.occurrence_id, status="cancelled")
    page.refresh()
    cancelled = page.grid.item(row, 0)
    assert cancelled is not None
    assert not bool(cancelled.flags() & Qt.ItemFlag.ItemIsUserCheckable)
    assert cancelled.background().color().name().upper() == "#F2F4F7"
    page.close()
