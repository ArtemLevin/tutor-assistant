from __future__ import annotations

from datetime import date, datetime

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from tutor_assistant.crm import CrmStore, ScheduledLesson  # noqa: E402
from tutor_assistant.domain import Student  # noqa: E402
from tutor_assistant.ui.schedule_ux_stable import ScheduleDialogStable  # noqa: E402


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_one_off_dialog_exposes_cancel_and_explicit_delete(
    tmp_path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    store.sync_students([Student(id="one-off", full_name="Разовое")])
    store.save_one_off(
        ScheduledLesson(
            student_id="one-off",
            student_name="Разовое",
            starts_at=datetime(2026, 8, 5, 17, 0),
            duration_minutes=60,
            subject="mathematics",
        )
    )
    lesson = store.lessons_for_week(date(2026, 8, 3))[0]

    dialog = ScheduleDialogStable(store, lesson.starts_at.date(), lesson=lesson)
    buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}

    assert buttons["Отменить занятие"].isEnabled()
    assert buttons["Удалить запись"].isEnabled()
    assert "Удалить серию" not in buttons
    dialog.close()
