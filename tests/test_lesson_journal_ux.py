from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from tutor_assistant.crm import CrmStore, ScheduledLesson, ScheduleRule, StudentProfile
from tutor_assistant.ui.lesson_journal_ux import JournalSmartView, LessonJournalUXPage


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
        return int(
            db.execute("SELECT COUNT(*) FROM crm_lesson_occurrences").fetchone()[0]
        )


def _show_range(page: LessonJournalUXPage, start: date, end: date) -> None:
    page.restore_filter_state(
        {
            "period": "custom",
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        }
    )


def _lesson(
    starts_at: datetime,
    *,
    paid: bool = False,
    lesson_id: str | None = None,
) -> ScheduledLesson:
    return ScheduledLesson(
        student_id="student",
        student_name="Ученик",
        starts_at=starts_at,
        duration_minutes=60,
        subject="mathematics",
        topic="Метод интервалов",
        rate_cents=300_000,
        paid=paid,
        lesson_id=lesson_id,
    )


def test_ux_journal_uses_compact_table_and_month_default(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-ux-columns.sqlite3", TestCodec())
    page = LessonJournalUXPage(store)

    headers = [
        page.table.horizontalHeaderItem(index).text()
        for index in range(page.table.columnCount())
    ]
    assert headers == [
        "Когда",
        "Ученик",
        "Занятие",
        "Статус",
        "Оплата",
        "ДЗ",
        "Ресурсы",
    ]
    assert page.table.columnCount() == 7
    assert page.period_filter.currentData() == "this_month"
    assert not page.payment_filter.isVisible()
    assert not page.homework_filter.isVisible()
    page.close()


def test_unpaid_smart_view_includes_future_unpaid(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-ux-unpaid.sqlite3", TestCodec())
    store.save_student(StudentProfile(id="student", full_name="Ученик"), [])
    today = date.today()
    past = datetime.combine(today - timedelta(days=2), time(16, 0))
    future = datetime.combine(today + timedelta(days=2), time(16, 0))
    paid = datetime.combine(today - timedelta(days=1), time(16, 0))
    store.save_one_off(_lesson(past))
    store.save_one_off(_lesson(future))
    store.save_one_off(_lesson(paid, paid=True))

    page = LessonJournalUXPage(store)
    _show_range(page, today - timedelta(days=7), today + timedelta(days=7))
    page.apply_smart_view("unpaid")
    application.processEvents()

    assert page.current_view == JournalSmartView.UNPAID
    assert page._smart_buttons[JournalSmartView.UNPAID].isChecked()
    assert page.payment_filter.currentData() == "all"
    assert {row.lesson.starts_at for row in page._rows} == {past, future}
    page.close()


def test_manual_payment_filter_exits_conflicting_smart_view(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-ux-conflict.sqlite3", TestCodec())
    page = LessonJournalUXPage(store)

    page.apply_smart_view("unpaid")
    page.payment_filter.setCurrentIndex(page.payment_filter.findData("paid"))
    application.processEvents()

    assert page.current_view == JournalSmartView.ALL
    assert page._smart_buttons[JournalSmartView.ALL].isChecked()
    assert page.payment_filter.currentData() == "paid"
    page.close()


def test_inline_payment_preserves_selected_lesson(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-ux-selection.sqlite3", TestCodec())
    store.save_student(StudentProfile(id="student", full_name="Ученик"), [])
    today = date.today()
    for offset in (1, 2, 3):
        store.save_one_off(
            _lesson(datetime.combine(today - timedelta(days=offset), time(16, 0)))
        )

    page = LessonJournalUXPage(store)
    _show_range(page, today - timedelta(days=7), today)
    page.table.selectRow(1)
    selected = page._selected_row()
    assert selected is not None
    starts_at = selected.lesson.starts_at

    page.table.item(1, 4).setCheckState(Qt.CheckState.Checked)
    application.processEvents()

    selected = page._selected_row()
    assert selected is not None
    assert selected.lesson.starts_at == starts_at
    page.close()


def test_recurring_materialization_preserves_logical_selection(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-ux-recurring.sqlite3", TestCodec())
    store.save_student(
        StudentProfile(id="student", full_name="Ученик", subjects=["mathematics"]),
        [],
    )
    monday = date.today() - timedelta(days=date.today().weekday())
    store.save_schedule_rule(
        ScheduleRule(
            student_id="student",
            weekday=0,
            start_minute=16 * 60,
            subject="mathematics",
            valid_from=monday,
            rate_cents=300_000,
        )
    )

    page = LessonJournalUXPage(store)
    _show_range(page, monday, monday + timedelta(days=6))
    assert page.table.rowCount() == 1
    original = page._selected_row()
    assert original is not None
    starts_at = original.lesson.starts_at
    assert original.lesson.occurrence_id is None

    page.table.item(0, 4).setCheckState(Qt.CheckState.Checked)
    application.processEvents()

    selected = page._selected_row()
    assert selected is not None
    assert selected.lesson.starts_at == starts_at
    assert selected.lesson.occurrence_id is not None
    assert _occurrence_count(store) == 1
    page.close()


def test_smart_view_row_removal_keeps_neighbour_context(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-ux-removal.sqlite3", TestCodec())
    store.save_student(StudentProfile(id="student", full_name="Ученик"), [])
    today = date.today()
    for offset in (1, 2, 3):
        store.save_one_off(
            _lesson(datetime.combine(today - timedelta(days=offset), time(16, 0)))
        )

    page = LessonJournalUXPage(store)
    _show_range(page, today - timedelta(days=7), today)
    page.apply_smart_view("unpaid")
    page.table.selectRow(1)

    page.table.item(1, 4).setCheckState(Qt.CheckState.Checked)
    application.processEvents()

    assert page.table.rowCount() == 2
    assert page.table.currentRow() == 1
    page.close()


def test_advanced_filters_have_count_and_persist_expanded_state(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-ux-filters.sqlite3", TestCodec())
    page = LessonJournalUXPage(store)

    page.filters_toggle.setChecked(True)
    page.payment_filter.setCurrentIndex(page.payment_filter.findData("unpaid_past"))
    page.homework_filter.setCurrentIndex(page.homework_filter.findData("review"))
    page.refresh()
    assert page.filters_toggle.text() == "Фильтры · 2"
    assert page.payment_filter.isVisible()
    page.close()

    restored = LessonJournalUXPage(store)
    assert restored.filters_toggle.isChecked()
    assert restored.payment_filter.isVisible()
    assert restored.payment_filter.currentData() == "unpaid_past"
    assert restored.homework_filter.currentData() == "review"
    restored.close()


def test_filter_chips_remove_only_selected_constraint(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-ux-chips.sqlite3", TestCodec())
    store.save_student(
        StudentProfile(id="student", full_name="Ученик", subjects=["mathematics"]),
        [],
    )
    page = LessonJournalUXPage(store)
    page.student_filter.setCurrentIndex(page.student_filter.findData("student"))
    page.subject_filter.setCurrentIndex(page.subject_filter.findData("mathematics"))
    page.payment_filter.setCurrentIndex(page.payment_filter.findData("unpaid_past"))
    page._update_filter_ui()

    labels = [button.text() for button in page._chip_buttons]
    assert any("Ученик" in label for label in labels)
    assert any("Математика" in label for label in labels)
    assert any("Есть задолженность" in label for label in labels)

    chip = next(button for button in page._chip_buttons if "Математика" in button.text())
    chip.click()
    application.processEvents()

    assert page.subject_filter.currentData() == ""
    assert page.student_filter.currentData() == "student"
    assert page.payment_filter.currentData() == "unpaid_past"
    page.close()


def test_summary_uses_semantic_tones(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-ux-summary.sqlite3", TestCodec())
    page = LessonJournalUXPage(store)

    assert page.summary_lessons.property("tone") == "neutral"
    assert page.summary_students.property("tone") == "neutral"
    assert page.summary_paid.property("tone") == "success"
    assert page.summary_unpaid.property("tone") == "error"
    assert page.summary_homework_review.property("tone") == "warning"
    assert page.summary_attention.property("tone") == "error"
    page.close()


def test_search_query_is_not_persisted_between_sessions(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-ux-search.sqlite3", TestCodec())
    page = LessonJournalUXPage(store)
    page.search.setText("старый запрос")
    page.refresh()
    page.close()

    restored = LessonJournalUXPage(store)
    assert restored.search.text() == ""
    restored.close()
