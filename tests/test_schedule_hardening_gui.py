from __future__ import annotations

from datetime import date, datetime

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QTime  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton  # noqa: E402

from tutor_assistant.crm import CrmStore, ScheduledLesson  # noqa: E402
from tutor_assistant.domain import Student  # noqa: E402
from tutor_assistant.schedule_status import ScheduledLessonStatus  # noqa: E402
from tutor_assistant.ui.schedule_ux_stable import (  # noqa: E402
    ScheduleDialogStable,
    SchedulePageStable,
)


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _store(tmp_path) -> CrmStore:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    store.sync_students([Student(id="student", full_name="Ученик")])
    return store


def _button(dialog: ScheduleDialogStable, text: str) -> QPushButton:
    return next(button for button in dialog.findChildren(QPushButton) if button.text() == text)


def test_new_lesson_must_end_inside_visible_workday(
    tmp_path,
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    dialog = ScheduleDialogStable(store, date(2026, 8, 3), 20, 0)
    dialog.start_time.setTime(QTime(20, 0))
    dialog.duration.setCurrentIndex(dialog.duration.findData(120))
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message, *_args, **_kwargs: warnings.append(message),
    )

    dialog._finish("save")

    assert dialog.result() != QDialog.Accepted
    assert warnings and "не позже 21:00" in warnings[-1]
    dialog.close()


def test_last_hour_accepts_one_hour_lesson(
    tmp_path,
    application: QApplication,
) -> None:
    store = _store(tmp_path)
    dialog = ScheduleDialogStable(store, date(2026, 8, 3), 20, 0)
    dialog.start_time.setTime(QTime(20, 0))
    dialog.duration.setCurrentIndex(dialog.duration.findData(60))

    dialog._finish("save")

    assert dialog.result() == QDialog.Accepted
    dialog.close()


def test_recording_failed_lesson_has_no_future_cancel_action(
    tmp_path,
    application: QApplication,
) -> None:
    store = _store(tmp_path)
    occurrence_id = store.save_one_off(
        ScheduledLesson(
            student_id="student",
            student_name="Ученик",
            starts_at=datetime(2026, 8, 5, 17, 0),
            duration_minutes=60,
            subject="mathematics",
        )
    )
    store.update_occurrence(
        occurrence_id,
        status=ScheduledLessonStatus.RECORDING_FAILED.value,
        lesson_id="lesson-recording-failed",
    )
    failed = store.lessons_for_week(date(2026, 8, 3))[0]

    dialog = ScheduleDialogStable(store, failed.starts_at.date(), lesson=failed)

    assert not _button(dialog, "Начать запись").isEnabled()
    assert not _button(dialog, "Отмена недоступна").isEnabled()
    assert all(
        button.text() != "Отменить занятие" for button in dialog.findChildren(QPushButton)
    )
    dialog.close()


def test_legacy_out_of_grid_lesson_is_reported_in_schedule_summary(
    tmp_path,
    application: QApplication,
) -> None:
    store = _store(tmp_path)
    store.save_one_off(
        ScheduledLesson(
            student_id="student",
            student_name="Ученик",
            starts_at=datetime(2026, 8, 5, 21, 0),
            duration_minutes=60,
            subject="mathematics",
        )
    )
    page = SchedulePageStable(store)
    page.week_start = date(2026, 8, 3)
    page.refresh()

    assert "вне сетки 1" in page.lessons_stat.text()
    assert "не полностью помещаются" in page.lessons_stat.toolTip()
    page.close()
