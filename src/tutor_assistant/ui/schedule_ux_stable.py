from __future__ import annotations

import logging
from datetime import date

from PySide6.QtCore import QSignalBlocker, Qt, QTime, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QMessageBox, QPushButton

from ..crm import ScheduleConflict, ScheduledLesson, ScheduleRule
from ..lesson_journal import HomeworkStatus
from ..schedule_status import (
    ScheduledLessonStatus,
    delete_one_off_lesson,
    set_scheduled_lesson_status,
    summarize_schedule,
)
from . import crm as base_crm
from .journal_interactions import ReversibleLessonJournalService
from .theme import set_button_kind

WORKDAY_FIRST_HOUR = 9
WORKDAY_LAST_HOUR = 20
SCHEDULE_SLOT_MINUTES = 60


class ScheduleDialogStable(base_crm.ScheduleDialog):
    """Stable schedule editor with explicit status and detail-only metadata controls."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.metadata_changed = False
        self.start_time.setToolTip("Шаг расписания — один час")
        if self.lesson is None:
            self.start_time.setTimeRange(
                QTime(WORKDAY_FIRST_HOUR, 0),
                QTime(WORKDAY_LAST_HOUR, 0),
            )
            return

        self.paid = QCheckBox("Оплачено")
        self.paid.setChecked(self.lesson.paid)
        self.paid.setToolTip("Изменение сохраняется сразу для выбранного занятия")
        self.paid.toggled.connect(self._payment_toggled)

        self.homework_received = QCheckBox("ДЗ получено")
        self.homework_received.setToolTip(
            "Изменение сохраняется сразу и синхронизируется с Журналом занятий"
        )
        self._homework_service = ReversibleLessonJournalService(self.store)
        self._homework_received = False
        try:
            homework = self._homework_service.snapshot_homework(self.lesson)
        except Exception:
            logging.getLogger(__name__).exception(
                "Schedule lesson details failed to read homework state"
            )
            self.homework_received.setEnabled(False)
            self.homework_received.setToolTip(
                "Состояние ДЗ временно недоступно; повторите после обновления расписания"
            )
        else:
            self._homework_received = homework.received_at is not None
            self.homework_received.setChecked(self._homework_received)
            self.homework_received.toggled.connect(self._homework_toggled)

        if self.lesson.status == ScheduledLessonStatus.CANCELLED.value:
            self.paid.setEnabled(False)
            self.homework_received.setEnabled(False)
            self.paid.setToolTip("Для отменённого занятия оплата доступна после восстановления")
            self.homework_received.setToolTip(
                "Для отменённого занятия отметка ДЗ доступна после восстановления"
            )

        root_layout = self.layout()
        if root_layout is not None:
            details = QHBoxLayout()
            details.addWidget(self.paid)
            details.addWidget(self.homework_received)
            details.addStretch(1)
            root_layout.insertLayout(max(0, root_layout.count() - 1), details)

        rule = (
            self.store.get_schedule_rule(self.lesson.rule_id)
            if self.lesson.rule_id is not None
            else None
        )
        active_series = bool(rule and rule.active)
        if self.lesson.rule_id is not None and not active_series:
            self.recurring.setChecked(False)
            self.recurring.setEnabled(False)
            self.recurring.setToolTip(
                "Эта повторяющаяся серия завершена. Для нового повторения создайте новую серию."
            )

        action_buttons = {button.text(): button for button in self.findChildren(QPushButton)}
        start = action_buttons.get("Начать запись")
        destructive = action_buttons.get("Удалить")
        if start is not None and self.lesson.status != ScheduledLessonStatus.PLANNED.value:
            start.setEnabled(False)
            start.setToolTip(
                "Запустить запись можно только для запланированного занятия"
            )
        if destructive is None:
            return
        try:
            destructive.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass

        if self.lesson.status == ScheduledLessonStatus.CANCELLED.value:
            destructive.setText("Вернуть занятие")
            destructive.setToolTip("Вернуть только это занятие; повторяющаяся серия не изменится")
            set_button_kind(destructive, "primary")
            if rule is not None and rule.valid_until is not None:
                original_date = self.lesson.original_date or self.lesson.starts_at.date()
                if original_date > rule.valid_until:
                    destructive.setEnabled(False)
                    destructive.setToolTip(
                        "Эта дата находится после завершения серии; создайте новое занятие"
                    )
            destructive.clicked.connect(lambda: self._finish("restore"))
        elif self.lesson.status in {
            ScheduledLessonStatus.IN_PROGRESS.value,
            ScheduledLessonStatus.COMPLETED.value,
        }:
            destructive.setText("Отмена недоступна")
            destructive.setEnabled(False)
            destructive.setToolTip("Начатое или завершённое занятие нельзя отменить как будущее")
            set_button_kind(destructive, "ghost")
        else:
            destructive.setText("Отменить занятие")
            destructive.setToolTip("Отменить только выбранную дату; будущие занятия серии сохранятся")
            set_button_kind(destructive, "danger")
            destructive.clicked.connect(lambda: self._finish("cancel_lesson"))

        actions = root_layout.itemAt(root_layout.count() - 1).layout() if root_layout else None

        if self.lesson.rule_id is not None and active_series:
            series_button = set_button_kind(QPushButton("Удалить серию"), "ghost")
            series_button.setToolTip(
                "Завершить повторяющуюся серию с выбранной даты, сохраняя историю"
            )
            series_button.clicked.connect(lambda: self._finish("delete_series"))
            if actions is not None:
                index = actions.indexOf(destructive)
                actions.insertWidget(index + 1 if index >= 0 else 0, series_button)

        if (
            self.lesson.rule_id is None
            and self.lesson.occurrence_id is not None
            and self.lesson.status
            in {
                ScheduledLessonStatus.PLANNED.value,
                ScheduledLessonStatus.CANCELLED.value,
            }
        ):
            delete_button = set_button_kind(QPushButton("Удалить запись"), "ghost")
            delete_button.setToolTip(
                "Безвозвратно удалить ошибочно созданное разовое занятие из расписания"
            )
            delete_button.clicked.connect(lambda: self._finish("delete_one_off"))
            if actions is not None:
                index = actions.indexOf(destructive)
                actions.insertWidget(index + 1 if index >= 0 else 0, delete_button)

    def _snap_start_time(self) -> None:
        clock = self.start_time.time()
        if (
            self.lesson is not None
            and clock.hour() == self.lesson.starts_at.hour
            and clock.minute() == self.lesson.starts_at.minute
        ):
            return
        total = clock.hour() * 60 + clock.minute()
        rounded_hour = (total + 30) // 60
        rounded_hour = max(WORKDAY_FIRST_HOUR, min(WORKDAY_LAST_HOUR, rounded_hour))
        self.start_time.setTime(QTime(rounded_hour, 0))

    def value(self) -> ScheduledLesson:
        self._snap_start_time()
        return super().value()

    def _payment_toggled(self, paid: bool) -> None:
        if self.lesson is None or paid == self.lesson.paid:
            return
        previous = self.lesson.paid
        try:
            occurrence_id = self.store.set_lesson_paid(self.lesson, paid)
        except Exception as exc:
            blocker = QSignalBlocker(self.paid)
            self.paid.setChecked(previous)
            del blocker
            QMessageBox.critical(
                self,
                "Оплата занятия",
                f"Не удалось сохранить состояние оплаты: {exc}",
            )
            return
        self.lesson.occurrence_id = occurrence_id
        self.lesson.paid = paid
        self.metadata_changed = True

    def _homework_toggled(self, received: bool) -> None:
        if self.lesson is None or received == self._homework_received:
            return
        previous = self._homework_received
        target = HomeworkStatus.RECEIVED if received else HomeworkStatus.SENT
        try:
            occurrence_id = self._homework_service.set_homework_status(self.lesson, target)
        except Exception as exc:
            blocker = QSignalBlocker(self.homework_received)
            self.homework_received.setChecked(previous)
            del blocker
            QMessageBox.critical(
                self,
                "Домашняя работа",
                f"Не удалось сохранить отметку о получении ДЗ: {exc}",
            )
            return
        self.lesson.occurrence_id = occurrence_id
        self._homework_received = received
        self.metadata_changed = True


class SchedulePageStable(base_crm.SchedulePage):
    """Compact workday schedule with durable history and explicit detail editing."""

    metadata_changed = Signal()

    first_hour = WORKDAY_FIRST_HOUR
    last_hour = WORKDAY_LAST_HOUR
    slot_minutes = SCHEDULE_SLOT_MINUTES

    def __init__(self, *args, **kwargs) -> None:
        self.cancelled_cell_lessons: dict[tuple[int, int], ScheduledLesson] = {}
        super().__init__(*args, **kwargs)

    def _build(self) -> None:
        super()._build()
        self.open_selected_button.setAccessibleDescription(
            "Открыть существующее занятие или создать новое в выбранном свободном слоте"
        )
        self.restore_cancelled_button = set_button_kind(
            QPushButton("Вернуть отменённое"),
            "ghost",
        )
        self.restore_cancelled_button.setVisible(False)
        self.restore_cancelled_button.setToolTip(
            "Открыть отменённое занятие отдельно от создания нового в свободном слоте"
        )
        self.restore_cancelled_button.clicked.connect(self._restore_selected_cancelled)
        root_layout = self.layout()
        header = root_layout.itemAt(0).layout() if root_layout is not None else None
        if header is not None:
            header.addWidget(self.restore_cancelled_button)

    @classmethod
    def _row_for_time(cls, hour: int, minute: int) -> int:
        """Map legacy half-hour starts into their containing hourly row without clamping."""

        offset = hour * 60 + minute - cls.first_hour * 60
        return offset // cls.slot_minutes

    @classmethod
    def _row_span_for_lesson(cls, lesson: ScheduledLesson) -> int:
        row = cls._row_for_time(lesson.starts_at.hour, lesson.starts_at.minute)
        row_start = cls.first_hour * 60 + row * cls.slot_minutes
        lesson_start = lesson.starts_at.hour * 60 + lesson.starts_at.minute
        start_offset = max(0, lesson_start - row_start)
        return max(
            1,
            (start_offset + lesson.duration_minutes + cls.slot_minutes - 1)
            // cls.slot_minutes,
        )

    def _cancelled_lesson_is_restorable(self, lesson: ScheduledLesson) -> bool:
        if lesson.lesson_id is not None:
            return False
        if lesson.rule_id is None:
            return True
        rule = self.store.get_schedule_rule(lesson.rule_id)
        if rule is None or not rule.active:
            return False
        original_date = lesson.original_date or lesson.starts_at.date()
        return rule.valid_until is None or original_date <= rule.valid_until

    def _clear_cancelled_grid_cells(self) -> None:
        """Rebuild hourly occupancy while keeping cancellation history outside the active grid."""

        rendered = dict(self.cell_lessons)
        if not rendered:
            return

        unique_lessons: list[ScheduledLesson] = []
        seen_lessons: set[int] = set()
        for lesson in rendered.values():
            lesson_key = id(lesson)
            if lesson_key in seen_lessons:
                continue
            seen_lessons.add(lesson_key)
            unique_lessons.append(lesson)

        signal_blocker = QSignalBlocker(self.grid)
        restoreable_cache: dict[int, bool] = {}
        self.grid.clearSpans()
        self.cell_lessons.clear()
        for lesson in unique_lessons:
            top_row = self._row_for_time(lesson.starts_at.hour, lesson.starts_at.minute)
            column = lesson.starts_at.weekday()
            if not (0 <= top_row < self.grid.rowCount()):
                continue
            row_span = min(
                self._row_span_for_lesson(lesson),
                self.grid.rowCount() - top_row,
            )
            if lesson.status == ScheduledLessonStatus.CANCELLED.value:
                if self.grid.item(top_row, column) is not None:
                    self.grid.takeItem(top_row, column)
                lesson_key = id(lesson)
                if lesson_key not in restoreable_cache:
                    restoreable_cache[lesson_key] = self._cancelled_lesson_is_restorable(lesson)
                if restoreable_cache[lesson_key]:
                    for occupied_row in range(top_row, top_row + row_span):
                        self.cancelled_cell_lessons[(occupied_row, column)] = lesson
                continue

            for occupied_row in range(top_row, top_row + row_span):
                self.cell_lessons[(occupied_row, column)] = lesson
            if row_span > 1:
                self.grid.setSpan(top_row, column, row_span, 1)
        del signal_blocker
        self._sync_schedule_action()

    def _compact_lesson_cells(self) -> None:
        """Render only the student name in calendar cells; all metadata lives in the dialog."""

        signal_blocker = QSignalBlocker(self.grid)
        try:
            seen: set[tuple[int, int]] = set()
            for (row, column), lesson in self.cell_lessons.items():
                top_row = self._row_for_time(lesson.starts_at.hour, lesson.starts_at.minute)
                position = (top_row, column)
                if position in seen or row != top_row:
                    continue
                seen.add(position)
                item = self.grid.item(top_row, column)
                if item is None:
                    continue
                item.setText(lesson.student_name)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.CheckStateRole, None)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                item.setToolTip("Дважды щёлкните левой кнопкой, чтобы открыть занятие")
        finally:
            del signal_blocker

    def refresh(self) -> None:
        self.cancelled_cell_lessons.clear()
        super().refresh()
        self._clear_cancelled_grid_cells()
        self._compact_lesson_cells()
        summary = summarize_schedule(self.store.lessons_for_week(self.week_start))
        self.lessons_stat.setText(
            f"Занятия · {summary.active_lessons} · отменено {summary.cancelled_lessons}"
        )
        self.lessons_stat.setToolTip(
            f"Всего записей на неделю: {summary.total_lessons}. "
            "Отменённые занятия не входят в плановую выручку, сохраняются в истории "
            "и не занимают ячейки расписания."
        )

    def _sync_schedule_action(self, *_args) -> None:
        super()._sync_schedule_action(*_args)
        row = self.grid.currentRow()
        column = self.grid.currentColumn()
        position = (row, column)
        can_restore = (
            row >= 0
            and column >= 0
            and position not in self.cell_lessons
            and position in self.cancelled_cell_lessons
        )
        self.restore_cancelled_button.setVisible(can_restore)
        self.restore_cancelled_button.setEnabled(can_restore)
        if can_restore:
            self.open_selected_button.setToolTip(
                "Создать новое занятие в свободном слоте; старую отмену можно вернуть отдельно"
            )
        else:
            self.open_selected_button.setToolTip("")

    def _cell_opened(self, row: int, column: int) -> None:
        selected_date = self.week_start + date.resolution * column
        selected_hour, selected_minute = self._time_for_row(row)
        self._open_dialog(
            selected_date,
            selected_hour,
            selected_minute,
            self.cell_lessons.get((row, column)),
        )

    def _restore_selected_cancelled(self) -> None:
        row = self.grid.currentRow()
        column = self.grid.currentColumn()
        if row < 0 or column < 0:
            return
        lesson = self.cancelled_cell_lessons.get((row, column))
        if lesson is None or (row, column) in self.cell_lessons:
            return
        selected_date = self.week_start + date.resolution * column
        selected_hour, selected_minute = self._time_for_row(row)
        self._open_dialog(
            selected_date,
            selected_hour,
            selected_minute,
            lesson,
        )

    def _cancelled_lesson_for_slot(
        self,
        selected_date: date,
        selected_hour: int,
        selected_minute: int,
    ) -> ScheduledLesson | None:
        row = self._row_for_time(selected_hour, selected_minute)
        position = (row, selected_date.weekday())
        if position in self.cell_lessons:
            return None
        return self.cancelled_cell_lessons.get(position)

    def _open_dialog(
        self,
        selected_date: date,
        selected_hour: int,
        selected_minute: int = 0,
        lesson: ScheduledLesson | None = None,
    ) -> None:
        replacing_cancelled = (
            lesson is None
            and self._cancelled_lesson_for_slot(
                selected_date,
                selected_hour,
                selected_minute,
            )
            is not None
        )
        dialog = ScheduleDialogStable(
            self.store,
            selected_date,
            selected_hour,
            selected_minute,
            lesson,
            self,
        )
        if replacing_cancelled:
            dialog.recurring.setChecked(False)
            dialog.recurring.setToolTip(
                "Свободный слот после отмены по умолчанию создаёт разовое занятие, "
                "чтобы не конфликтовать с исходной серией"
            )
        accepted = dialog.exec() == QDialog.Accepted
        if dialog.metadata_changed:
            self.refresh()
            self.metadata_changed.emit()
        if not accepted:
            return
        value = dialog.value()
        try:
            if dialog.action == "start":
                if value.status != ScheduledLessonStatus.PLANNED.value:
                    QMessageBox.warning(
                        self,
                        "Расписание",
                        "Запустить запись можно только для запланированного занятия.",
                    )
                    return
                occurrence_id = self.store.ensure_occurrence(value)
                self.start_requested.emit(
                    occurrence_id,
                    value.student_id,
                    value.subject,
                    value.topic or value.subject,
                )
                return
            if dialog.action == "cancel_lesson":
                set_scheduled_lesson_status(
                    self.store,
                    value,
                    ScheduledLessonStatus.CANCELLED,
                )
            elif dialog.action == "restore":
                set_scheduled_lesson_status(
                    self.store,
                    value,
                    ScheduledLessonStatus.PLANNED,
                )
            elif dialog.action == "delete_one_off":
                answer = QMessageBox.question(
                    self,
                    "Удалить разовое занятие",
                    "Безвозвратно удалить эту запись расписания? Связанные отметки оплаты и ДЗ "
                    "этой записи тоже будут удалены. Проведённые занятия удалить этим действием нельзя.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
                delete_one_off_lesson(self.store, value)
            elif dialog.action == "delete_series":
                if value.rule_id is None:
                    return
                cutoff = value.original_date or value.starts_at.date()
                answer = QMessageBox.question(
                    self,
                    "Удалить серию",
                    "Завершить повторяющуюся серию с выбранной даты? Будущие запланированные "
                    "занятия будут отменены, а прошлые занятия, оплата, ДЗ и история сохранятся.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
                self.store.end_schedule_rule(value.rule_id, effective_from=cutoff)
            elif dialog.recurring.isChecked():
                existing_rule = (
                    self.store.get_schedule_rule(value.rule_id)
                    if value.rule_id is not None
                    else None
                )
                if existing_rule is not None and not existing_rule.active:
                    raise ValueError(
                        "Завершённую серию нельзя неявно включить снова. Создайте новую серию."
                    )
                self.store.save_schedule_rule(
                    ScheduleRule(
                        id=value.rule_id,
                        student_id=value.student_id,
                        weekday=value.starts_at.weekday(),
                        start_minute=value.starts_at.hour * 60 + value.starts_at.minute,
                        duration_minutes=value.duration_minutes,
                        subject=value.subject,
                        topic=value.topic,
                        meeting_url=value.meeting_url,
                        valid_from=(
                            existing_rule.valid_from if existing_rule else value.starts_at.date()
                        ),
                        valid_until=existing_rule.valid_until if existing_rule else None,
                        rate_cents=value.rate_cents,
                    )
                )
            elif lesson:
                occurrence_id = self.store.ensure_occurrence(value)
                self.store.update_occurrence_details(occurrence_id, value)
            else:
                self.store.save_one_off(value)
        except ScheduleConflict as exc:
            QMessageBox.warning(self, "Конфликт расписания", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Расписание", str(exc))
            return
        self.refresh()