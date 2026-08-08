from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from tutor_assistant.crm import CrmStore, ScheduledLesson, StudentProfile
from tutor_assistant.ui.journal_widgets import (
    ATTENTION_TEXT_ROLE,
    STATUS_TEXT_ROLE,
    STATUS_TONE_ROLE,
)
from tutor_assistant.ui.lesson_journal_interactions import LessonJournalInteractionPage


class TestCodec:
    def encrypt(self, value: str | None) -> str | None:
        return value

    def decrypt(self, value: str | None) -> str | None:
        return value


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def clean_settings() -> None:
    settings = QSettings("TutorAssistant", "TutorAssistant")
    settings.remove("ux6/journal")
    settings.sync()
    yield
    settings.remove("ux6/journal")
    settings.sync()


def _page(tmp_path: Path) -> LessonJournalInteractionPage:
    store = CrmStore(tmp_path / "accessibility.sqlite3", TestCodec())
    store.save_student(
        StudentProfile(id="student", full_name="Ученик", subjects=["mathematics"]),
        [],
    )
    today = date.today()
    lesson = ScheduledLesson(
        student_id="student",
        student_name="Ученик",
        starts_at=datetime.combine(today - timedelta(days=1), time(16, 0)),
        duration_minutes=60,
        subject="mathematics",
        topic="Квадратные уравнения",
        status="completed",
        rate_cents=300_000,
        paid=False,
    )
    store.save_one_off(lesson)
    page = LessonJournalInteractionPage(store)
    page.restore_filter_state(
        {
            "period": "custom",
            "date_from": (today - timedelta(days=3)).isoformat(),
            "date_to": today.isoformat(),
        }
    )
    return page


def test_interactive_controls_have_accessible_names(
    tmp_path: Path,
    application: QApplication,
) -> None:
    page = _page(tmp_path)
    controls = (
        page.search,
        page.student_filter,
        page.subject_filter,
        page.period_filter,
        page.filters_toggle,
        page.payment_filter,
        page.homework_filter,
        page.status_filter,
        page.time_enabled,
        page.time_from,
        page.time_to,
        page.detail_payment,
        page.detail_homework,
        page.due_enabled,
        page.due_at,
        page.save_due_button,
        page.open_lesson_button,
        page.open_materials_button,
        page.open_schedule_button,
    )
    assert all(widget.accessibleName().strip() for widget in controls)
    assert page.table.accessibleName() == "Журнал занятий"
    assert "Enter" in page.table.accessibleDescription()
    page.close()


def test_status_payment_and_resources_expose_full_accessible_text(
    tmp_path: Path,
    application: QApplication,
) -> None:
    page = _page(tmp_path)
    status = page.table.item(0, 3)
    payment = page.table.item(0, 4)
    resources = page.table.item(0, 6)

    assert status.data(STATUS_TEXT_ROLE) == "Завершено"
    assert status.data(STATUS_TONE_ROLE) == "success"
    assert status.data(ATTENTION_TEXT_ROLE)
    assert "задолженность" in str(
        status.data(Qt.ItemDataRole.AccessibleTextRole)
    ).casefold()
    assert "задолженность" in str(
        payment.data(Qt.ItemDataRole.AccessibleTextRole)
    ).casefold()
    resource_text = str(resources.data(Qt.ItemDataRole.AccessibleTextRole)).casefold()
    assert "запись" in resource_text
    assert "транскрипт" in resource_text
    assert "материалы" in resource_text
    page.close()


def test_filter_chips_remain_keyboard_descriptive(
    tmp_path: Path,
    application: QApplication,
) -> None:
    page = _page(tmp_path)
    page.subject_filter.setCurrentIndex(page.subject_filter.findData("mathematics"))
    page._update_filter_ui()

    assert page._chip_buttons
    assert all("Удалить фильтр" in chip.accessibleName() for chip in page._chip_buttons)
    page.close()


def test_empty_state_has_programmatic_description(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "accessibility-empty.sqlite3", TestCodec())
    store.save_student(StudentProfile(id="student", full_name="Ученик"), [])
    page = LessonJournalInteractionPage(store)
    application.processEvents()

    assert page.table_stack.currentWidget() is page.empty_state
    assert page.empty_state.accessibleName()
    assert page.empty_state.accessibleDescription()
    page.close()


def test_keyboard_focus_zones_are_exposed_and_focusable(
    tmp_path: Path,
    application: QApplication,
) -> None:
    page = _page(tmp_path)
    page.show()
    application.processEvents()
    zones = page.keyboard_focus_zones()

    assert len(zones) >= 4
    page.search.setFocus()
    application.processEvents()
    page.keyboard.cycle_focus(1)
    application.processEvents()
    assert page.table.hasFocus()
    page.close()
