from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton

from ..crm import ScheduleConflict, ScheduledLesson, ScheduleRule
from ..schedule_status import (
    ScheduledLessonStatus,
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
        action_buttons = {button.text(): button for button in self.findChildren(QPushButton)}
        start = action_buttons.get("Начать запись")
        destructive = action_buttons.get("Удалить")
        if start is not None and self.lesson.status == ScheduledLessonStatus.CANCELLED.value:
            start.setEnabled(False)
            start.setToolTip("Сначала верните отменённое занятие в расписание")
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
            destructive.clicked.connect(lambda: self._finish("restore"))
        else:
            destructive.setText("Отменить занятие")
            destructive.setToolTip("Отменить только выбранную дату; будущие занятия серии сохранятся")
            set_button_kind(destructive, "danger")
            destructive.clicked.connect(lambda: self._finish("cancel_lesson"))

        if self.lesson.rule_id is not None:
            series_button = set_button_kind(QPushButton("Удалить серию"), "ghost")
            series_button.setToolTip("Остановить всю повторяющуюся серию, включая будущие занятия")
            series_button.clicked.connect(lambda: self._finish("delete_series"))
            root_layout = self.layout()
            actions = root_layout.itemAt(root_layout.count() - 1).layout() if root_layout else None
            if actions is not None:
                index = actions.indexOf(destructive)
                actions.insertWidget(index + 1 if index >= 0 else 0, series_button)


class SchedulePageStable(base_crm.SchedulePage):
    """Schedule page with explicit cancellation accounting and atomic status changes."""

    def refresh(self) -> None:
        super().refresh()
        summary = summarize_schedule(self.store.lessons_for_week(self.week_start))
        self.lessons_stat.setText(
            f"Занятия · {summary.active_lessons} · отменено {summary.cancelled_lessons}"
        )
        self.lessons_stat.setToolTip(
            f"Всего записей на неделю: {summary.total_lessons}. "
            "Отменённые занятия не входят в плановую выручку."
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
                if value.status == ScheduledLessonStatus.CANCELLED.value:
                    QMessageBox.warning(
                        self,
                        "Расписание",
                        "Отменённое занятие нельзя начать. Сначала верните его в расписание.",
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
            elif dialog.action == "delete_series":
                if value.rule_id is None:
                    return
                answer = QMessageBox.question(
                    self,
                    "Удалить серию",
                    "Остановить всю повторяющуюся серию? Будущие занятия этой серии исчезнут "
                    "из расписания. Уже материализованные занятия и история сохранятся.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
                self.store.delete_schedule_rule(value.rule_id)
            elif dialog.recurring.isChecked():
                existing_rule = next(
                    (item for item in self.store.list_schedule_rules() if item.id == value.rule_id),
                    None,
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
