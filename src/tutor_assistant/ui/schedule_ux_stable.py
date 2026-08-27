from __future__ import annotations

from datetime import date

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton

from ..crm import ScheduleConflict, ScheduledLesson, ScheduleRule
from ..schedule_status import (
    ScheduledLessonStatus,
    delete_one_off_lesson,
    set_scheduled_lesson_status,
    summarize_schedule,
)
from . import crm as base_crm
from .theme import set_button_kind


class ScheduleDialogStable(base_crm.ScheduleDialog):
    """Make occurrence cancellation explicit while preserving series management."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self.lesson is None:
            return

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

        root_layout = self.layout()
        actions = root_layout.itemAt(root_layout.count() - 1).layout() if root_layout else None

        if self.lesson.rule_id is not None and active_series:
            series_button = set_button_kind(QPushButton("Удалить серию"), "ghost")
            series_button.setToolTip(
                "Завершить повторяющуюся серию с выбранной даты, сохранив историю"
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


class SchedulePageStable(base_crm.SchedulePage):
    """Schedule page with explicit cancellation accounting and atomic status changes."""

    def _clear_cancelled_grid_cells(self) -> None:
        """Keep cancellation history without letting it occupy active schedule slots."""

        rendered = dict(self.cell_lessons)
        if not any(
            lesson.status == ScheduledLessonStatus.CANCELLED.value
            for lesson in rendered.values()
        ):
            return

        signal_blocker = QSignalBlocker(self.grid)
        self.grid.clearSpans()
        self.cell_lessons.clear()
        for (row, column), lesson in rendered.items():
            if lesson.status == ScheduledLessonStatus.CANCELLED.value:
                if self.grid.item(row, column) is not None:
                    self.grid.takeItem(row, column)
                continue

            self.cell_lessons[(row, column)] = lesson
            top_row = self._row_for_time(lesson.starts_at.hour, lesson.starts_at.minute)
            if row != top_row:
                continue
            row_span = max(
                1,
                (lesson.duration_minutes + self.slot_minutes - 1) // self.slot_minutes,
            )
            row_span = min(row_span, self.grid.rowCount() - top_row)
            if row_span > 1:
                self.grid.setSpan(top_row, column, row_span, 1)
        del signal_blocker
        self._sync_schedule_action()

    def refresh(self) -> None:
        super().refresh()
        self._clear_cancelled_grid_cells()
        summary = summarize_schedule(self.store.lessons_for_week(self.week_start))
        self.lessons_stat.setText(
            f"Занятия · {summary.active_lessons} · отменено {summary.cancelled_lessons}"
        )
        self.lessons_stat.setToolTip(
            f"Всего записей на неделю: {summary.total_lessons}. "
            "Отменённые занятия сохраняются в истории, но не занимают ячейки расписания."
        )

    def _open_dialog(
        self,
        selected_date: date,
        selected_hour: int,
        selected_minute: int = 0,
        lesson: ScheduledLesson | None = None,
    ) -> None:
        dialog = ScheduleDialogStable(
            self.store,
            selected_date,
            selected_hour,
            selected_minute,
            lesson,
            self,
        )
        if dialog.exec() != QDialog.Accepted:
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
