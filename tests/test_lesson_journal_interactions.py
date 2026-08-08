from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox

from tutor_assistant.crm import CrmStore, ScheduledLesson, StudentProfile
from tutor_assistant.lesson_journal import HomeworkStatus
from tutor_assistant.ui.journal_interactions import ReversibleLessonJournalService
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


def _lesson(starts_at: datetime, *, paid: bool = False) -> ScheduledLesson:
    return ScheduledLesson(
        student_id="student",
        student_name="Ученик",
        starts_at=starts_at,
        duration_minutes=60,
        subject="mathematics",
        topic="Производная",
        rate_cents=300_000,
        paid=paid,
    )


def _store(tmp_path: Path, name: str) -> CrmStore:
    store = CrmStore(tmp_path / name, TestCodec())
    store.save_student(
        StudentProfile(id="student", full_name="Ученик", subjects=["mathematics"]),
        [],
    )
    return store


def _show_range(page: LessonJournalInteractionPage, start: date, end: date) -> None:
    page.restore_filter_state(
        {
            "period": "custom",
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        }
    )


def test_payment_mutation_has_one_step_undo(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path, "interaction-payment.sqlite3")
    today = date.today()
    starts_at = datetime.combine(today - timedelta(days=1), time(16, 0))
    store.save_one_off(_lesson(starts_at))
    page = LessonJournalInteractionPage(store)
    _show_range(page, today - timedelta(days=3), today)

    item = page.table.item(0, 4)
    item.setCheckState(Qt.CheckState.Checked)
    application.processEvents()

    assert page._rows[0].lesson.paid
    assert page.toast.isVisible()
    assert page._pending_undo is not None

    page.undo_last_action()
    application.processEvents()

    assert not page._rows[0].lesson.paid
    assert page._pending_undo is None
    assert "отменено" in page.toast.message.text().casefold()
    page.close()


def test_homework_snapshot_restores_exact_timestamps(tmp_path: Path) -> None:
    store = _store(tmp_path, "interaction-homework-snapshot.sqlite3")
    today = date.today()
    store.save_one_off(_lesson(datetime.combine(today, time(16, 0))))
    lesson = store.lessons_for_week(today - timedelta(days=today.weekday()))[0]
    service = ReversibleLessonJournalService(store)
    original_at = datetime(2026, 8, 8, 10, 15, 0)
    service.set_homework_status(lesson, HomeworkStatus.RETURNED, at=original_at)
    snapshot = service.snapshot_homework(lesson)

    service.set_homework_status(
        lesson,
        HomeworkStatus.SENT,
        at=datetime(2026, 8, 8, 12, 0, 0),
    )
    service.restore_homework(lesson, snapshot)
    restored = service.snapshot_homework(lesson)

    assert restored.returned_at == original_at
    assert restored.checked_at == original_at
    assert restored.received_at == original_at


def test_due_date_change_can_be_undone(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path, "interaction-due.sqlite3")
    today = date.today()
    starts_at = datetime.combine(today, time(15, 0))
    store.save_one_off(_lesson(starts_at))
    page = LessonJournalInteractionPage(store)
    _show_range(page, today, today)

    page.due_enabled.setChecked(True)
    target = starts_at + timedelta(days=5)
    page.due_at.setDateTime(target)
    page._save_due()
    application.processEvents()
    assert page._rows[0].homework_due_at is not None

    page.undo_last_action()
    application.processEvents()
    assert page._rows[0].homework_due_at is None
    page.close()


def test_contextual_empty_states(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path, "interaction-empty.sqlite3")
    page = LessonJournalInteractionPage(store)
    application.processEvents()

    assert page.table_stack.currentWidget() is page.empty_state
    assert "занятий нет" in page.empty_state.title.text().casefold()

    page.apply_smart_view("unpaid")
    application.processEvents()
    assert "неоплаченных" in page.empty_state.title.text().casefold()

    page.apply_smart_view("attention")
    application.processEvents()
    assert "в порядке" in page.empty_state.title.text().casefold()
    page.close()


def test_keyboard_shortcuts_cover_primary_workflow(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path, "interaction-keyboard.sqlite3")
    today = date.today()
    store.save_one_off(_lesson(datetime.combine(today, time(16, 0))))
    page = LessonJournalInteractionPage(store)
    _show_range(page, today, today)
    page.show()
    application.processEvents()

    page.table.setFocus()
    QTest.keyClick(page, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    application.processEvents()
    assert page.search.hasFocus()

    page.table.setFocus()
    QTest.keyClick(page.table, Qt.Key.Key_F2)
    application.processEvents()
    homework = page.table.cellWidget(0, 5)
    assert isinstance(homework, QComboBox)
    assert homework.hasFocus()

    page.table.setFocus()
    QTest.keyClick(page.table, Qt.Key.Key_Space)
    application.processEvents()
    assert page._rows[0].lesson.paid

    QTest.keyClick(page, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    application.processEvents()
    assert not page._rows[0].lesson.paid
    page.close()


def test_enter_activates_selected_lesson_route(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path, "interaction-enter.sqlite3")
    today = date.today()
    store.save_one_off(_lesson(datetime.combine(today, time(17, 0))))
    page = LessonJournalInteractionPage(store)
    _show_range(page, today, today)
    emitted: list[object] = []
    page.show_in_schedule_requested.connect(emitted.append)
    page.table.setFocus()

    QTest.keyClick(page.table, Qt.Key.Key_Return)
    application.processEvents()

    assert emitted
    assert isinstance(emitted[0], datetime)
    page.close()


def test_toast_does_not_steal_table_focus(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path, "interaction-focus.sqlite3")
    today = date.today()
    store.save_one_off(_lesson(datetime.combine(today, time(18, 0))))
    page = LessonJournalInteractionPage(store)
    _show_range(page, today, today)
    page.show()
    page.table.setFocus()
    application.processEvents()

    page.toggle_current_payment()
    application.processEvents()

    assert page.toast.isVisible()
    assert page.table.hasFocus()
    page.close()
