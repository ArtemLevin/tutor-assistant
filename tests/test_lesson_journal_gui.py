from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QComboBox

from tutor_assistant.crm import CrmStore, ScheduledLesson, ScheduleRule, StudentProfile
from tutor_assistant.ui.app_routes import AppRoute, page_for_route, route_for_page
from tutor_assistant.ui.lesson_journal import LessonJournalPage


class TestCodec:
    def encrypt(self, value: str | None) -> str | None:
        return value

    def decrypt(self, value: str | None) -> str | None:
        return value


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def clean_journal_settings() -> None:
    settings = QSettings("TutorAssistant", "TutorAssistant")
    settings.remove("ux6/journal")
    settings.sync()
    yield
    settings.remove("ux6/journal")
    settings.sync()


def _occurrence_count(store: CrmStore) -> int:
    with sqlite3.connect(store.path) as db:
        return int(db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0])


def _show_week(page: LessonJournalPage) -> None:
    page.restore_filter_state(
        {
            "period": "custom",
            "date_from": "2026-08-03",
            "date_to": "2026-08-09",
        }
    )


def test_journal_route_preserves_existing_page_indices() -> None:
    assert page_for_route(AppRoute.MATERIALS) == 7
    assert page_for_route(AppRoute.TODAY) == 8
    assert page_for_route(AppRoute.JOURNAL) == 9
    assert route_for_page(9) == AppRoute.JOURNAL


def test_journal_render_does_not_materialize_recurring_lesson(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-gui.sqlite3", TestCodec())
    store.save_student(
        StudentProfile(id="student", full_name="Ученик", subjects=["mathematics"]),
        [],
    )
    store.save_schedule_rule(
        ScheduleRule(
            student_id="student",
            weekday=0,
            start_minute=16 * 60,
            duration_minutes=90,
            subject="mathematics",
            topic="Метод интервалов",
            valid_from=date(2026, 8, 1),
            rate_cents=300_000,
        )
    )

    page = LessonJournalPage(store)
    _show_week(page)
    page.show()
    application.processEvents()

    assert page.table.rowCount() == 1
    assert page.table.item(0, 2).text() == "Ученик"
    assert page.table.item(0, 3).text() == "Математика"
    assert page.table.item(0, 6).checkState() == Qt.CheckState.Unchecked
    assert _occurrence_count(store) == 0
    page.close()


def test_journal_quick_statuses_materialize_and_persist(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-actions.sqlite3", TestCodec())
    store.save_student(
        StudentProfile(id="student", full_name="Ученик", subjects=["chemistry"]),
        [],
    )
    store.save_schedule_rule(
        ScheduleRule(
            student_id="student",
            weekday=1,
            start_minute=17 * 60,
            subject="chemistry",
            valid_from=date(2026, 8, 1),
            rate_cents=250_000,
        )
    )
    page = LessonJournalPage(store)
    _show_week(page)
    page.show()
    application.processEvents()

    payment = page.table.item(0, 6)
    payment.setCheckState(Qt.CheckState.Checked)
    application.processEvents()
    assert _occurrence_count(store) == 1
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT paid FROM crm_lesson_occurrences").fetchone()[0] == 1

    homework = page.table.cellWidget(0, 7)
    assert isinstance(homework, QComboBox)
    homework.setCurrentIndex(homework.findData("received"))
    application.processEvents()
    with sqlite3.connect(store.path) as db:
        row = db.execute(
            "SELECT assigned_at, sent_at, received_at, checked_at "
            "FROM crm_lesson_homework"
        ).fetchone()
    assert row[0] and row[1] and row[2]
    assert row[3] is None

    page.refresh()
    assert page.table.item(0, 6).checkState() == Qt.CheckState.Checked
    restored_homework = page.table.cellWidget(0, 7)
    assert isinstance(restored_homework, QComboBox)
    assert restored_homework.currentData() == "received"
    page.close()


def test_smart_views_and_navigation_signals(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-views.sqlite3", TestCodec())
    store.save_student(StudentProfile(id="student", full_name="Ученик"), [])
    store.save_one_off(
        ScheduledLesson(
            student_id="student",
            student_name="Ученик",
            starts_at=datetime(2026, 8, 3, 16, 0),
            duration_minutes=60,
            subject="mathematics",
            topic="Урок",
            rate_cents=300_000,
            lesson_id="lesson-123",
        )
    )
    page = LessonJournalPage(store)
    _show_week(page)
    page.show()
    application.processEvents()

    page.apply_smart_view("unpaid")
    assert page.table.rowCount() == 1
    assert page.payment_filter.currentData() == "unpaid_past"

    lessons: list[str] = []
    materials: list[str] = []
    schedule: list[object] = []
    page.open_lesson_requested.connect(lessons.append)
    page.open_materials_requested.connect(materials.append)
    page.show_in_schedule_requested.connect(schedule.append)
    page.table.selectRow(0)
    page._open_selected_lesson()
    page._open_selected_materials()
    page._open_selected_schedule()
    assert lessons == ["lesson-123"]
    assert materials == ["student"]
    assert schedule == [datetime(2026, 8, 3, 16, 0)]
    page.close()
