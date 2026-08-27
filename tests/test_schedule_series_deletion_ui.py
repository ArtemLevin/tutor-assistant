from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from tutor_assistant.crm import CrmStore, ScheduleRule  # noqa: E402
from tutor_assistant.domain import Student  # noqa: E402
from tutor_assistant.schedule_status import ScheduledLessonStatus  # noqa: E402
from tutor_assistant.ui.schedule_ux_stable import ScheduleDialogStable  # noqa: E402


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _lesson(store: CrmStore):
    store.sync_students([Student(id="ui-series", full_name="UI серия")])
    rule_id = store.save_schedule_rule(
        ScheduleRule(
            student_id="ui-series",
            weekday=2,
            start_minute=16 * 60,
            valid_from=date(2026, 8, 1),
            rate_cents=250_000,
        )
    )
    return rule_id, store.lessons_for_week(date(2026, 8, 10))[0]


def _button(dialog: ScheduleDialogStable, text: str) -> QPushButton:
    return next(button for button in dialog.findChildren(QPushButton) if button.text() == text)


def test_ended_series_occurrence_cannot_restore_or_resurrect_series(
    tmp_path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    rule_id, lesson = _lesson(store)
    store.set_lesson_paid(lesson, True)
    store.end_schedule_rule(rule_id, effective_from=date(2026, 8, 12))
    cancelled = store.lessons_for_week(date(2026, 8, 10))[0]

    dialog = ScheduleDialogStable(store, cancelled.starts_at.date(), lesson=cancelled)

    assert cancelled.status == ScheduledLessonStatus.CANCELLED.value
    assert not dialog.recurring.isChecked()
    assert not dialog.recurring.isEnabled()
    assert not _button(dialog, "Вернуть занятие").isEnabled()
    assert all(
        button.text() != "Удалить серию" for button in dialog.findChildren(QPushButton)
    )
    dialog.close()


def test_completed_occurrence_has_no_cancel_or_start_action(
    tmp_path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    _rule_id, lesson = _lesson(store)
    occurrence_id = store.ensure_occurrence(lesson)
    store.update_occurrence(
        occurrence_id,
        status=ScheduledLessonStatus.COMPLETED.value,
        lesson_id="lesson-completed",
    )
    completed = store.lessons_for_week(date(2026, 8, 10))[0]

    dialog = ScheduleDialogStable(store, completed.starts_at.date(), lesson=completed)

    assert not _button(dialog, "Начать запись").isEnabled()
    assert not _button(dialog, "Отмена недоступна").isEnabled()
    assert all(
        button.text() != "Отменить занятие" for button in dialog.findChildren(QPushButton)
    )
    dialog.close()
