from __future__ import annotations

from datetime import date, datetime

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication  # noqa: E402

from tutor_assistant.crm import CrmStore, ScheduledLesson, ScheduleRule  # noqa: E402
from tutor_assistant.domain import Student  # noqa: E402
from tutor_assistant.schedule_status import (  # noqa: E402
    ScheduledLessonStatus,
    delete_one_off_lesson,
    set_scheduled_lesson_status,
)
from tutor_assistant.ui.schedule_ux_stable import SchedulePageStable  # noqa: E402


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _store(tmp_path) -> CrmStore:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    store.sync_students(
        [
            Student(id="series", full_name="Серия"),
            Student(id="one-off", full_name="Разовое"),
            Student(id="replacement", full_name="Замена"),
        ]
    )
    return store


def _series_lesson(store: CrmStore, week_start: date) -> tuple[int, ScheduledLesson]:
    rule_id = store.save_schedule_rule(
        ScheduleRule(
            student_id="series",
            weekday=2,
            start_minute=16 * 60,
            duration_minutes=60,
            subject="mathematics",
            topic="Серия",
            valid_from=date(2026, 8, 1),
            rate_cents=300_000,
        )
    )
    return rule_id, store.lessons_for_week(week_start)[0]


def _grid_position(page: SchedulePageStable, lesson: ScheduledLesson) -> tuple[int, int]:
    return (
        page._row_for_time(lesson.starts_at.hour, lesson.starts_at.minute),
        lesson.starts_at.weekday(),
    )


def test_cancelled_occurrence_clears_schedule_cell_but_keeps_history(
    tmp_path,
    application: QApplication,
) -> None:
    store = _store(tmp_path)
    week = date(2026, 8, 3)
    _rule_id, lesson = _series_lesson(store, week)
    page = SchedulePageStable(store)
    page.week_start = week
    page.refresh()
    row, column = _grid_position(page, lesson)
    assert page.grid.item(row, column) is not None

    occurrence_id = set_scheduled_lesson_status(
        store,
        lesson,
        ScheduledLessonStatus.CANCELLED,
    )
    page.refresh()

    assert page.grid.item(row, column) is None
    assert (row, column) not in page.cell_lessons
    assert page.cancelled_cell_lessons[(row, column)].occurrence_id == occurrence_id
    page.grid.setCurrentCell(row, column)
    page._sync_schedule_action()
    assert page.open_selected_button.text() == "Вернуть отменённое"
    persisted = store.lessons_for_week(week)
    assert len(persisted) == 1
    assert persisted[0].occurrence_id == occurrence_id
    assert persisted[0].status == ScheduledLessonStatus.CANCELLED.value
    assert "отменено 1" in page.lessons_stat.text()
    assert "не занимают ячейки" in page.lessons_stat.toolTip()
    page.close()


def test_ending_series_clears_materialized_future_cell_without_losing_metadata(
    tmp_path,
    application: QApplication,
) -> None:
    store = _store(tmp_path)
    week = date(2026, 8, 10)
    rule_id, lesson = _series_lesson(store, week)
    occurrence_id = store.set_lesson_paid(lesson, True)
    page = SchedulePageStable(store)
    page.week_start = week
    page.refresh()
    row, column = _grid_position(page, lesson)
    assert page.grid.item(row, column) is not None

    store.end_schedule_rule(rule_id, effective_from=lesson.starts_at.date())
    page.refresh()

    assert page.grid.item(row, column) is None
    assert (row, column) not in page.cell_lessons
    assert (row, column) not in page.cancelled_cell_lessons
    page.grid.setCurrentCell(row, column)
    page._sync_schedule_action()
    assert page.open_selected_button.text() == "Создать в выбранное время"
    persisted = store.lessons_for_week(week)
    assert len(persisted) == 1
    assert persisted[0].occurrence_id == occurrence_id
    assert persisted[0].status == ScheduledLessonStatus.CANCELLED.value
    assert persisted[0].paid is True
    page.close()


def test_deleted_one_off_record_clears_schedule_cell(
    tmp_path,
    application: QApplication,
) -> None:
    store = _store(tmp_path)
    week = date(2026, 8, 3)
    store.save_one_off(
        ScheduledLesson(
            student_id="one-off",
            student_name="Разовое",
            starts_at=datetime(2026, 8, 5, 18, 0),
            duration_minutes=60,
            subject="mathematics",
            topic="Ошибка",
        )
    )
    lesson = store.lessons_for_week(week)[0]
    page = SchedulePageStable(store)
    page.week_start = week
    page.refresh()
    row, column = _grid_position(page, lesson)
    assert page.grid.item(row, column) is not None

    delete_one_off_lesson(store, lesson)
    page.refresh()

    assert page.grid.item(row, column) is None
    assert (row, column) not in page.cell_lessons
    assert (row, column) not in page.cancelled_cell_lessons
    assert store.lessons_for_week(week) == []
    page.close()


def test_cancelled_tombstone_does_not_hide_replacement_lesson(
    tmp_path,
    application: QApplication,
) -> None:
    store = _store(tmp_path)
    week = date(2026, 8, 3)
    _rule_id, lesson = _series_lesson(store, week)
    set_scheduled_lesson_status(store, lesson, ScheduledLessonStatus.CANCELLED)
    store.save_one_off(
        ScheduledLesson(
            student_id="replacement",
            student_name="Замена",
            starts_at=lesson.starts_at,
            duration_minutes=lesson.duration_minutes,
            subject="mathematics",
            topic="Новая запись",
        )
    )

    page = SchedulePageStable(store)
    page.week_start = week
    page.refresh()
    row, column = _grid_position(page, lesson)

    item = page.grid.item(row, column)
    assert item is not None
    assert "Замена" in item.text()
    assert page.cell_lessons[(row, column)].student_id == "replacement"
    page.grid.setCurrentCell(row, column)
    page._sync_schedule_action()
    assert page.open_selected_button.text() == "Открыть занятие"
    page.close()
