from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from tutor_assistant.crm import CrmStore, ScheduleRule  # noqa: E402
from tutor_assistant.domain import Student  # noqa: E402
from tutor_assistant.schedule_status import (  # noqa: E402
    ScheduledLessonStatus,
    set_scheduled_lesson_status,
)
from tutor_assistant.ui.schedule_ux_stable import (  # noqa: E402
    ScheduleDialogStable,
    SchedulePageStable,
)


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _lesson(store: CrmStore):
    store.sync_students([Student(id="ui-cancel", full_name="UI отмена")])
    store.save_schedule_rule(
        ScheduleRule(
            student_id="ui-cancel",
            weekday=2,
            start_minute=16 * 60,
            valid_from=date(2026, 8, 1),
            rate_cents=250_000,
        )
    )
    return store.lessons_for_week(date(2026, 8, 3))[0]


def _button(dialog: ScheduleDialogStable, text: str) -> QPushButton:
    return next(button for button in dialog.findChildren(QPushButton) if button.text() == text)


def test_planned_recurring_lesson_separates_occurrence_cancel_from_series_delete(
    tmp_path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    lesson = _lesson(store)
    dialog = ScheduleDialogStable(store, lesson.starts_at.date(), lesson=lesson)

    assert _button(dialog, "Отменить занятие").isEnabled()
    assert _button(dialog, "Удалить серию").isEnabled()
    assert all(button.text() != "Удалить" for button in dialog.findChildren(QPushButton))
    dialog.close()


def test_cancelled_lesson_exposes_restore_and_disables_start(
    tmp_path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    lesson = _lesson(store)
    set_scheduled_lesson_status(store, lesson, ScheduledLessonStatus.CANCELLED)
    cancelled = store.lessons_for_week(date(2026, 8, 3))[0]
    dialog = ScheduleDialogStable(store, cancelled.starts_at.date(), lesson=cancelled)

    assert _button(dialog, "Вернуть занятие").isEnabled()
    assert _button(dialog, "Удалить серию").isEnabled()
    assert not _button(dialog, "Начать запись").isEnabled()
    dialog.close()


def test_schedule_page_shows_cancelled_count(tmp_path, application: QApplication) -> None:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    lesson = _lesson(store)
    set_scheduled_lesson_status(store, lesson, ScheduledLessonStatus.CANCELLED)
    page = SchedulePageStable(store)
    page.week_start = date(2026, 8, 3)
    page.refresh()

    assert "отменено 1" in page.lessons_stat.text()
    assert "Отменённые занятия не входят" in page.lessons_stat.toolTip()
    page.close()
