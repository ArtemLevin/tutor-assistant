from __future__ import annotations

from datetime import date, datetime

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from tutor_assistant.crm import CrmStore, ScheduledLesson  # noqa: E402
from tutor_assistant.domain import Student  # noqa: E402
from tutor_assistant.schedule_status import delete_one_off_lesson  # noqa: E402
from tutor_assistant.ui import schedule_ux_stable as schedule_ui  # noqa: E402


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _store(tmp_path) -> CrmStore:
    store = CrmStore(tmp_path / "schedule-empty-slot.sqlite3")
    store.sync_students(
        [
            Student(id="old", full_name="Старое"),
            Student(id="replacement", full_name="Замена"),
            Student(id="future", full_name="Будущее"),
        ]
    )
    return store


def test_physically_deleted_empty_cell_creates_one_off_not_weekly_series(
    tmp_path,
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    week = date(2026, 8, 3)
    slot = datetime(2026, 8, 5, 16, 0)

    store.save_one_off(
        ScheduledLesson(
            student_id="old",
            student_name="Старое",
            starts_at=slot,
            duration_minutes=60,
            subject="mathematics",
        )
    )
    deleted = store.lessons_for_week(week)[0]
    delete_one_off_lesson(store, deleted)

    # A legitimate concrete lesson exists one week later at the same weekday/time.
    # If the empty-cell dialog silently stays recurring=True, saving the replacement
    # goes through save_schedule_rule() and raises the exact production conflict:
    # "В выбранное время уже назначено конкретное занятие".
    store.save_one_off(
        ScheduledLesson(
            student_id="future",
            student_name="Будущее",
            starts_at=datetime(2026, 8, 12, 16, 0),
            duration_minutes=60,
            subject="mathematics",
        )
    )

    page = schedule_ui.SchedulePageStable(store)
    page.week_start = week
    page.refresh()
    row = page._row_for_time(slot.hour, slot.minute)
    column = slot.weekday()
    assert page.grid.item(row, column) is None
    assert (row, column) not in page.cell_lessons
    assert (row, column) not in page.cancelled_cell_lessons

    real_dialog = schedule_ui.ScheduleDialogStable

    class FakeScheduleDialog(real_dialog):
        recurring_seen_on_exec: bool | None = None

        def __init__(
            self,
            _store,
            selected_date,
            selected_hour,
            selected_minute,
            lesson_arg,
            _parent,
        ) -> None:
            super().__init__(
                _store,
                selected_date,
                selected_hour,
                selected_minute,
                lesson_arg,
                _parent,
            )
            assert lesson_arg is None
            self.action = "save"
            self._value = ScheduledLesson(
                student_id="replacement",
                student_name="Замена",
                starts_at=datetime(
                    selected_date.year,
                    selected_date.month,
                    selected_date.day,
                    selected_hour,
                    selected_minute,
                ),
                duration_minutes=60,
                subject="mathematics",
            )

        def exec(self) -> int:
            type(self).recurring_seen_on_exec = self.recurring.isChecked()
            return QDialog.Accepted

        def value(self) -> ScheduledLesson:
            return self._value

    warnings: list[str] = []
    monkeypatch.setattr(schedule_ui, "ScheduleDialogStable", FakeScheduleDialog)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message, *_args, **_kwargs: warnings.append(message),
    )

    page._cell_opened(row, column)

    assert FakeScheduleDialog.recurring_seen_on_exec is False
    assert warnings == []
    current = store.lessons_for_week(week)
    assert len(current) == 1
    assert current[0].student_id == "replacement"
    assert current[0].rule_id is None
    future = store.lessons_for_week(date(2026, 8, 10))
    assert len(future) == 1
    assert future[0].student_id == "future"
    assert future[0].rule_id is None
    page.close()
