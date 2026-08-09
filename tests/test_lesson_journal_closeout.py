from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tutor_assistant.crm import CrmStore, ScheduledLesson, StudentProfile
from tutor_assistant.lesson_closeout import AttendanceStatus
from tutor_assistant.ui.journal_closeout import CloseoutJournalFilter
from tutor_assistant.ui.lesson_journal_closeout_stable import LessonJournalCloseoutStablePage


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


def _store(tmp_path: Path, name: str) -> CrmStore:
    store = CrmStore(tmp_path / name, TestCodec())
    store.save_student(
        StudentProfile(id="student", full_name="Ученик", subjects=["mathematics"]),
        [],
    )
    return store


def _lesson(starts_at: datetime, *, topic: str = "Производная") -> ScheduledLesson:
    return ScheduledLesson(
        student_id="student",
        student_name="Ученик",
        starts_at=starts_at,
        duration_minutes=60,
        subject="mathematics",
        topic=topic,
        rate_cents=300_000,
    )


def _show_range(page: LessonJournalCloseoutStablePage, start: date, end: date) -> None:
    page.restore_filter_state(
        {
            "period": "custom",
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        }
    )
    page.refresh()


def test_closeout_query_filters_attendance_and_unfinished(tmp_path: Path) -> None:
    store = _store(tmp_path, "closeout-query.sqlite3")
    today = date.today()
    first = _lesson(datetime.combine(today - timedelta(days=2), time(16, 0)), topic="A")
    second = _lesson(datetime.combine(today - timedelta(days=1), time(16, 0)), topic="B")
    store.save_one_off(first)
    store.save_one_off(second)
    page = LessonJournalCloseoutStablePage(store)
    page.closeout_service.close_lesson(
        first,
        attendance=AttendanceStatus.PRESENT,
        teacher_note="Готово",
    )

    present = page.service.search(
        CloseoutJournalFilter(
            date_from=today - timedelta(days=3),
            date_to=today,
            attendance=AttendanceStatus.PRESENT.value,
        )
    )
    unfinished = page.service.search(
        CloseoutJournalFilter(
            date_from=today - timedelta(days=3),
            date_to=today,
            unfinished_only=True,
        )
    )

    assert present.total == 1
    assert present.rows[0].lesson.topic == "A"
    assert unfinished.total == 1
    assert unfinished.rows[0].lesson.topic == "B"
    assert unfinished.summary.unfinished == 1
    page.close()


def test_closeout_controls_dirty_state_and_accessibility(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path, "closeout-controls.sqlite3")
    today = date.today()
    store.save_one_off(_lesson(datetime.combine(today - timedelta(days=1), time(16, 0))))
    page = LessonJournalCloseoutStablePage(store)
    _show_range(page, today - timedelta(days=2), today)
    page.show()
    application.processEvents()

    assert page.detail_attendance.isEnabled()
    assert page.teacher_note.isEnabled()
    assert page.detail_attendance.accessibleName()
    assert page.teacher_note.accessibleName()
    assert page.close_lesson_button.accessibleName()
    assert page.attendance_filter.accessibleName()

    page.teacher_note.setPlainText("Есть прогресс по теме")
    application.processEvents()
    assert "несохранённые" in page.note_state.text().casefold()
    assert page.save_closeout_button.isEnabled()

    page.resize(1024, 720)
    application.processEvents()
    assert page.teacher_note.width() > 100
    assert page.close_lesson_button.width() > 80
    page.close()


def test_attendance_change_and_closeout_support_undo(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path, "closeout-undo-gui.sqlite3")
    today = date.today()
    starts_at = datetime.combine(today - timedelta(days=1), time(16, 0))
    store.save_one_off(_lesson(starts_at))
    page = LessonJournalCloseoutStablePage(store)
    _show_range(page, today - timedelta(days=2), today)
    page.show()
    application.processEvents()

    page.detail_attendance.setCurrentIndex(
        page.detail_attendance.findData(AttendanceStatus.PRESENT.value)
    )
    application.processEvents()
    assert page._rows[0].attendance == AttendanceStatus.PRESENT

    page.teacher_note.setPlainText("Урок закрыт")
    page.close_current_lesson()
    application.processEvents()

    assert page._rows[0].lesson.status == "completed"
    assert page._rows[0].closeout is not None
    assert page._rows[0].closeout.teacher_note == "Урок закрыт"
    assert page._pending_undo is not None

    page.undo_last_action()
    application.processEvents()

    assert page._rows[0].lesson.status == "planned"
    assert page._rows[0].attendance == AttendanceStatus.PRESENT
    assert page._rows[0].closeout is not None
    assert page._rows[0].closeout.closed_at is None
    page.close()


def test_unfinished_smart_view_removes_closed_row_and_restores_on_undo(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path, "closeout-unfinished.sqlite3")
    today = date.today()
    store.save_one_off(
        _lesson(datetime.combine(today - timedelta(days=2), time(16, 0)), topic="Первый")
    )
    store.save_one_off(
        _lesson(datetime.combine(today - timedelta(days=1), time(16, 0)), topic="Второй")
    )
    page = LessonJournalCloseoutStablePage(store)
    _show_range(page, today - timedelta(days=3), today)
    page.apply_unfinished_view()
    page.show()
    application.processEvents()
    assert len(page._rows) == 2

    selected_identity = page._identity(page._rows[0])
    page.detail_attendance.setCurrentIndex(
        page.detail_attendance.findData(AttendanceStatus.PRESENT.value)
    )
    application.processEvents()
    page.close_current_lesson()
    application.processEvents()
    assert len(page._rows) == 1

    page.undo_last_action()
    application.processEvents()
    assert len(page._rows) == 2
    assert page._identity(page._selected_row()) == selected_identity
    page.close()


def test_dirty_note_is_saved_when_switching_rows(
    tmp_path: Path,
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path, "closeout-dirty-switch.sqlite3")
    today = date.today()
    store.save_one_off(
        _lesson(datetime.combine(today - timedelta(days=2), time(16, 0)), topic="Первый")
    )
    store.save_one_off(
        _lesson(datetime.combine(today - timedelta(days=1), time(16, 0)), topic="Второй")
    )
    page = LessonJournalCloseoutStablePage(store)
    _show_range(page, today - timedelta(days=3), today)
    page.show()
    application.processEvents()
    original = page._rows[0].lesson

    page.teacher_note.setPlainText("Сохранить при переходе")
    monkeypatch.setattr(page, "_confirm_dirty_transition", lambda: "save")
    page.table.selectRow(1)
    application.processEvents()

    saved = page.closeout_service.get_for_lesson(original)
    assert saved is not None
    assert saved.teacher_note == "Сохранить при переходе"
    assert not page._note_dirty
    page.close()


def test_attendance_filter_has_chip_and_resets_independently(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path, "closeout-filter-chip.sqlite3")
    page = LessonJournalCloseoutStablePage(store)
    page.attendance_filter.setCurrentIndex(
        page.attendance_filter.findData(AttendanceStatus.NO_SHOW.value)
    )
    application.processEvents()
    page._update_filter_ui()

    labels = [button.text() for button in page._chip_buttons]
    assert any("Посещаемость" in label and "Не пришёл" in label for label in labels)
    assert page._advanced_filter_count() >= 1

    attendance_chip = next(
        button for button in page._chip_buttons if "Посещаемость" in button.text()
    )
    attendance_chip.click()
    application.processEvents()
    assert page.attendance_filter.currentData() == "all"
    page.close()


def test_closeout_keyboard_shortcuts(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = _store(tmp_path, "closeout-keyboard.sqlite3")
    today = date.today()
    store.save_one_off(_lesson(datetime.combine(today - timedelta(days=1), time(16, 0))))
    page = LessonJournalCloseoutStablePage(store)
    _show_range(page, today - timedelta(days=2), today)
    page.show()
    application.processEvents()

    page.table.setFocus()
    QTest.keyClick(page, Qt.Key.Key_F3)
    application.processEvents()
    assert page.detail_attendance.hasFocus()

    page.detail_attendance.setCurrentIndex(
        page.detail_attendance.findData(AttendanceStatus.PRESENT.value)
    )
    application.processEvents()
    page.teacher_note.setPlainText("Сохранено с клавиатуры")
    QTest.keyClick(page, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    application.processEvents()
    assert not page._note_dirty

    QTest.keyClick(page, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    application.processEvents()
    assert page._rows[0].lesson.status == "completed"
    page.close()
