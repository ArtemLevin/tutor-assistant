from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from tutor_assistant.crm import CrmStore, ScheduledLesson, StudentProfile
from tutor_assistant.lesson_closeout import AttendanceStatus
from tutor_assistant.ui.journal_closeout import ReversibleCloseoutService
from tutor_assistant.ui.journal_widgets import (
    ATTENDANCE_TEXT_ROLE,
    ATTENTION_TEXT_ROLE,
)
from tutor_assistant.ui.lesson_journal_closeout_stable import LessonJournalCloseoutStablePage


class TestCodec:
    def encrypt(self, value: str | None) -> str | None:
        return value

    def decrypt(self, value: str | None) -> str | None:
        return value


class CountingCodec(TestCodec):
    def __init__(self) -> None:
        self.decrypt_calls = 0

    def decrypt(self, value: str | None) -> str | None:
        self.decrypt_calls += 1
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


def _store(tmp_path: Path, name: str, codec=None) -> CrmStore:
    store = CrmStore(tmp_path / name, codec or TestCodec())
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


def _show_past_lesson(
    tmp_path: Path,
    application: QApplication,
    name: str,
) -> tuple[CrmStore, ScheduledLesson, LessonJournalCloseoutStablePage]:
    store = _store(tmp_path, name)
    today = date.today()
    lesson = _lesson(datetime.combine(today - timedelta(days=1), time(16, 0)))
    store.save_one_off(lesson)
    page = LessonJournalCloseoutStablePage(store)
    page.restore_filter_state(
        {
            "period": "custom",
            "date_from": (today - timedelta(days=2)).isoformat(),
            "date_to": today.isoformat(),
        }
    )
    page.show()
    application.processEvents()
    page._cancel_pending_refresh()
    return store, lesson, page


def test_attendance_is_part_of_closeout_draft_until_save(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store, lesson, page = _show_past_lesson(
        tmp_path,
        application,
        "attendance-draft.sqlite3",
    )

    page.detail_attendance.setCurrentIndex(
        page.detail_attendance.findData(AttendanceStatus.PRESENT.value)
    )
    application.processEvents()

    assert page._rows[0].attendance == AttendanceStatus.PRESENT
    assert page.closeout_service.get_for_lesson(lesson) is None
    assert page._note_dirty
    assert page.save_closeout_button.isEnabled()

    page.save_closeout_draft()
    application.processEvents()
    saved = page.closeout_service.get_for_lesson(lesson)
    assert saved is not None
    assert saved.attendance == AttendanceStatus.PRESENT
    assert not page._note_dirty
    page.close()


def test_dirty_filter_stay_restores_applied_filter_and_keeps_draft(
    tmp_path: Path,
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_obj, _lesson_obj, page = _show_past_lesson(
        tmp_path,
        application,
        "dirty-filter-stay.sqlite3",
    )
    page.teacher_note.setPlainText("Не потерять этот текст")
    application.processEvents()
    monkeypatch.setattr(page, "_confirm_dirty_transition", lambda: "stay")

    page.search.setText("нет совпадений")
    page._cancel_pending_refresh()
    page.refresh()
    application.processEvents()

    assert page.search.text() == ""
    assert page.teacher_note.toPlainText() == "Не потерять этот текст"
    assert page._note_dirty
    assert len(page._rows) == 1
    page._discard_closeout_draft()
    page.close()


def test_dirty_filter_save_persists_before_context_change(
    tmp_path: Path,
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_obj, lesson, page = _show_past_lesson(
        tmp_path,
        application,
        "dirty-filter-save.sqlite3",
    )
    page.teacher_note.setPlainText("Сохранить перед фильтрацией")
    page.detail_attendance.setCurrentIndex(
        page.detail_attendance.findData(AttendanceStatus.LATE.value)
    )
    application.processEvents()
    monkeypatch.setattr(page, "_confirm_dirty_transition", lambda: "save")

    page.search.setText("нет совпадений")
    page._cancel_pending_refresh()
    page.refresh()
    application.processEvents()

    saved = page.closeout_service.get_for_lesson(lesson)
    assert saved is not None
    assert saved.teacher_note == "Сохранить перед фильтрацией"
    assert saved.attendance == AttendanceStatus.LATE
    assert not page._note_dirty
    assert not page._rows
    page.close()


def test_close_undo_reopens_with_latest_draft_values(
    tmp_path: Path,
    application: QApplication,
) -> None:
    _store_obj, lesson, page = _show_past_lesson(
        tmp_path,
        application,
        "close-undo-draft.sqlite3",
    )
    page.detail_attendance.setCurrentIndex(
        page.detail_attendance.findData(AttendanceStatus.PRESENT.value)
    )
    page.teacher_note.setPlainText("Новый итог, который должен сохраниться")
    application.processEvents()

    page.close_current_lesson()
    application.processEvents()
    page.undo_last_action()
    application.processEvents()

    reopened = page.closeout_service.get_for_lesson(lesson)
    assert reopened is not None
    assert reopened.attendance == AttendanceStatus.PRESENT
    assert reopened.teacher_note == "Новый итог, который должен сохраниться"
    assert reopened.closed_at is None
    assert page._rows[0].lesson.status == "planned"
    page.close()


def test_ctrl_z_inside_teacher_note_uses_native_editor_undo(
    tmp_path: Path,
    application: QApplication,
) -> None:
    _store_obj, _lesson_obj, page = _show_past_lesson(
        tmp_path,
        application,
        "native-text-undo.sqlite3",
    )
    page.detail_payment.setChecked(True)
    application.processEvents()
    assert page._pending_undo is not None
    assert page._rows[0].lesson.paid

    page.teacher_note.setFocus()
    QTest.keyClicks(page.teacher_note, "abc")
    application.processEvents()
    assert page.teacher_note.toPlainText() == "abc"

    QTest.keyClick(
        page.teacher_note,
        Qt.Key.Key_Z,
        Qt.KeyboardModifier.ControlModifier,
    )
    application.processEvents()

    assert page.teacher_note.toPlainText() == ""
    assert page._rows[0].lesson.paid
    assert page._pending_undo is not None
    page.undo_last_action()
    application.processEvents()
    page.close()


def test_saving_closeout_invalidates_older_journal_undo(
    tmp_path: Path,
    application: QApplication,
) -> None:
    _store_obj, _lesson_obj, page = _show_past_lesson(
        tmp_path,
        application,
        "stale-undo.sqlite3",
    )
    page.detail_payment.setChecked(True)
    application.processEvents()
    assert page._pending_undo is not None

    page.teacher_note.setPlainText("Новый самостоятельный save")
    page.save_closeout_draft()
    application.processEvents()

    assert page._pending_undo is None
    assert not page.toast.undo_button.isVisible()
    page.close()


def test_attention_and_attendance_have_independent_visual_roles(
    tmp_path: Path,
    application: QApplication,
) -> None:
    _store_obj, lesson, page = _show_past_lesson(
        tmp_path,
        application,
        "status-roles.sqlite3",
    )
    page.closeout_service.save_draft(
        lesson,
        attendance=AttendanceStatus.EXCUSED,
        teacher_note="",
    )
    page.refresh(preserve_context=True)
    application.processEvents()

    item = page.table.item(0, 3)
    assert item is not None
    assert item.data(ATTENTION_TEXT_ROLE)
    assert item.data(ATTENDANCE_TEXT_ROLE) == "По договорённости"
    accessible = str(item.data(Qt.ItemDataRole.AccessibleTextRole) or "")
    assert "Требует внимания" in accessible
    assert "Посещаемость: По договорённости" in accessible

    delegate = page.table.itemDelegateForColumn(3)
    option = QStyleOptionViewItem()
    option.font = page.table.font()
    hint = delegate.sizeHint(option, page.table.model().index(0, 3))
    assert hint.height() >= 50
    assert hint.width() > 150
    page.close()


def test_focus_model_enters_closeout_at_attendance(
    tmp_path: Path,
    application: QApplication,
) -> None:
    _store_obj, _lesson_obj, page = _show_past_lesson(
        tmp_path,
        application,
        "focus-model.sqlite3",
    )
    zones = page.keyboard_focus_zones()
    assert zones[-1] is page.detail_attendance
    assert page.detail_payment not in zones
    assert page.table.nextInFocusChain() is page.detail_attendance
    page.close()


def test_dirty_page_close_can_be_cancelled(
    tmp_path: Path,
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_obj, _lesson_obj, page = _show_past_lesson(
        tmp_path,
        application,
        "close-guard.sqlite3",
    )
    page.teacher_note.setPlainText("Остаться на странице")
    application.processEvents()
    monkeypatch.setattr(page, "_confirm_dirty_transition", lambda: "stay")

    assert page.close() is False
    assert page.isVisible()
    page._discard_closeout_draft()
    assert page.close() is True


def test_table_state_listing_does_not_decrypt_teacher_notes(tmp_path: Path) -> None:
    codec = CountingCodec()
    store = _store(tmp_path, "lazy-note.sqlite3", codec=codec)
    lesson = _lesson(datetime.combine(date.today() - timedelta(days=1), time(16, 0)))
    occurrence_id = store.save_one_off(lesson)
    service = ReversibleCloseoutService(store)
    service.save_draft(
        lesson,
        attendance=AttendanceStatus.PRESENT,
        teacher_note="Секретная локальная заметка",
    )

    codec.decrypt_calls = 0
    states = service.list_states_for_occurrences({occurrence_id})
    assert states[occurrence_id].attendance == AttendanceStatus.PRESENT
    assert states[occurrence_id].teacher_note == ""
    assert codec.decrypt_calls == 0

    full = service.get_for_lesson(lesson)
    assert full is not None
    assert full.teacher_note == "Секретная локальная заметка"
    assert codec.decrypt_calls == 1
