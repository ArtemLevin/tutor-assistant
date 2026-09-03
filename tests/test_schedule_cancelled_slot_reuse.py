from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QTime  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from tutor_assistant.crm import CrmStore, ScheduledLesson, ScheduleRule  # noqa: E402
from tutor_assistant.domain import Student  # noqa: E402
from tutor_assistant.schedule_status import (  # noqa: E402
    ScheduledLessonStatus,
    set_scheduled_lesson_status,
)
from tutor_assistant.ui import schedule_ux_stable as schedule_ui  # noqa: E402


class _RecurringControl:
    def __init__(self) -> None:
        self.checked = True
        self.tooltip = ""

    def isChecked(self) -> bool:
        return self.checked

    def setChecked(self, checked: bool) -> None:
        self.checked = checked

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _store(tmp_path) -> CrmStore:
    store = CrmStore(tmp_path / "schedule-reuse.sqlite3")
    store.sync_students(
        [
            Student(id="series", full_name="Серия"),
            Student(id="replacement", full_name="Замена"),
        ]
    )
    return store


def test_schedule_workday_includes_nine_to_ten_slot(
    tmp_path,
    application: QApplication,
) -> None:
    store = _store(tmp_path)
    page = schedule_ui.SchedulePageStable(store)

    assert page.grid.rowCount() == 12
    assert page.grid.verticalHeaderItem(0).text() == "09:00"
    assert page.grid.verticalHeaderItem(11).text() == "20:00"
    assert page._time_for_row(0) == (9, 0)

    dialog = schedule_ui.ScheduleDialogStable(store, date(2026, 8, 3), 9, 0)
    assert dialog.start_time.minimumTime() == QTime(9, 0)
    assert dialog.start_time.maximumTime() == QTime(20, 0)
    dialog.close()
    page.close()


def test_cancelled_slot_double_click_creates_one_off_replacement(
    tmp_path,
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    week = date(2026, 8, 3)
    store.save_schedule_rule(
        ScheduleRule(
            student_id="series",
            weekday=2,
            start_minute=16 * 60,
            duration_minutes=60,
            subject="mathematics",
            topic="Исходная серия",
            valid_from=date(2026, 8, 1),
        )
    )
    lesson = store.lessons_for_week(week)[0]
    occurrence_id = set_scheduled_lesson_status(
        store,
        lesson,
        ScheduledLessonStatus.CANCELLED,
    )

    page = schedule_ui.SchedulePageStable(store)
    page.week_start = week
    page.refresh()
    row = page._row_for_time(lesson.starts_at.hour, lesson.starts_at.minute)
    column = lesson.starts_at.weekday()
    assert page.grid.item(row, column) is None
    assert page.cancelled_cell_lessons[(row, column)].occurrence_id == occurrence_id

    class FakeScheduleDialog:
        seen_lesson: ScheduledLesson | None = None

        def __init__(
            self,
            _store,
            selected_date,
            selected_hour,
            selected_minute,
            lesson_arg,
            _parent,
        ) -> None:
            type(self).seen_lesson = lesson_arg
            self.metadata_changed = False
            self.action = "save"
            self.recurring = _RecurringControl()
            self._value = ScheduledLesson(
                student_id="replacement",
                student_name="Замена",
                starts_at=lesson.starts_at.replace(
                    year=selected_date.year,
                    month=selected_date.month,
                    day=selected_date.day,
                    hour=selected_hour,
                    minute=selected_minute,
                ),
                duration_minutes=60,
                subject="mathematics",
                topic="Замена после отмены",
            )

        def exec(self) -> int:
            assert not self.recurring.isChecked()
            assert "разовое занятие" in self.recurring.tooltip
            return QDialog.Accepted

        def value(self) -> ScheduledLesson:
            return self._value

    monkeypatch.setattr(schedule_ui, "ScheduleDialogStable", FakeScheduleDialog)
    page._cell_opened(row, column)

    assert FakeScheduleDialog.seen_lesson is None
    persisted = store.lessons_for_week(week)
    cancelled = [item for item in persisted if item.status == ScheduledLessonStatus.CANCELLED.value]
    active = [item for item in persisted if item.status != ScheduledLessonStatus.CANCELLED.value]
    assert len(cancelled) == 1
    assert cancelled[0].occurrence_id == occurrence_id
    assert len(active) == 1
    assert active[0].student_id == "replacement"
    assert active[0].rule_id is None
    item = page.grid.item(row, column)
    assert item is not None
    assert item.text() == "Замена"
    assert page.cell_lessons[(row, column)].student_id == "replacement"
    page.close()
