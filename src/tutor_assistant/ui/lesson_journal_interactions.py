from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QSplitter,
    QStackedWidget,
    QTableWidgetItem,
    QWidget,
)

from ..crm import ScheduledLesson
from ..lesson_journal import HomeworkStatus, LessonJournalResult, LessonJournalRow
from .journal_interactions import JournalUndoAction, ReversibleLessonJournalService
from .journal_keyboard import JournalKeyboardController
from .journal_widgets import (
    ATTENTION_TEXT_ROLE,
    ATTENTION_TONE_ROLE,
    JournalEmptyState,
    JournalStatusDelegate,
    JournalStatusDescriptor,
    JournalToastBar,
    JournalTone,
    STATUS_TEXT_ROLE,
    STATUS_TONE_ROLE,
)
from .lesson_journal import LESSON_STATUS_LABELS
from .lesson_journal_ux import JournalSmartView, JournalViewAnchor
from .lesson_journal_ux_stable import LessonJournalUXStablePage


_STATUS_TONES = {
    "planned": JournalTone.NEUTRAL,
    "in_progress": JournalTone.INFO,
    "completed": JournalTone.SUCCESS,
    "cancelled": JournalTone.NEUTRAL,
    "recording_failed": JournalTone.ERROR,
}


class LessonJournalInteractionPage(LessonJournalUXStablePage):
    """Interaction and accessibility layer for the operational lesson journal."""

    def __init__(self, *args, **kwargs) -> None:
        self._interaction_ready = False
        self._pending_undo: JournalUndoAction | None = None
        self._pending_undo_focus: QWidget | None = None
        super().__init__(*args, **kwargs)
        lesson_store = getattr(self.service, "lesson_store", None)
        self.service = ReversibleLessonJournalService(self.store, lesson_store)
        self._install_interaction_layer()
        self._interaction_ready = True
        self.refresh(preserve_context=True)

    def _install_interaction_layer(self) -> None:
        self.setStyleSheet(
            self.styleSheet()
            + """
            QPushButton:focus, QComboBox:focus, QCheckBox:focus,
            QDateEdit:focus, QDateTimeEdit:focus, QTimeEdit:focus,
            QLineEdit:focus, QTableWidget:focus {
                border: 2px solid #275AA6;
            }
            QLabel#journalEmptyIcon {
                font-size: 30px;
                color: #8A98AA;
            }
            """
        )
        self.table.setAccessibleName("Журнал занятий")
        self.table.setAccessibleDescription(
            "Таблица занятий. Стрелки выбирают строку. Enter открывает занятие, "
            "пробел изменяет оплату, F2 переводит фокус к домашней работе."
        )
        self.table.setItemDelegateForColumn(3, JournalStatusDelegate(self.table))

        splitter = self.findChild(QSplitter, "lessonJournalSplitter")
        if splitter is None:
            raise RuntimeError("Не найден splitter журнала занятий")
        table_index = splitter.indexOf(self.table)
        self.table.setParent(None)
        self.table_stack = QStackedWidget()
        self.table_stack.setAccessibleName("Список занятий")
        self.table_stack.addWidget(self.table)
        self.empty_state = JournalEmptyState()
        self.empty_state.action_requested.connect(self._empty_action_requested)
        self.table_stack.addWidget(self.empty_state)
        splitter.insertWidget(max(0, table_index), self.table_stack)

        self.toast = JournalToastBar(self)
        self.toast.undo_requested.connect(self.undo_last_action)
        self.toast.dismissed.connect(self._expire_undo)

        self._install_accessibility_metadata()
        self._install_tab_order()
        self.keyboard = JournalKeyboardController(self)
        self._position_toast()

    def _install_accessibility_metadata(self) -> None:
        metadata = (
            (self.period_filter, "Период журнала занятий"),
            (self.date_from, "Начальная дата журнала"),
            (self.date_to, "Конечная дата журнала"),
            (self.payment_filter, "Фильтр по оплате"),
            (self.homework_filter, "Фильтр по статусу домашней работы"),
            (self.status_filter, "Фильтр по статусу занятия"),
            (self.time_enabled, "Включить фильтр по времени"),
            (self.time_from, "Начальное время занятий"),
            (self.time_to, "Конечное время занятий"),
            (self.sort_filter, "Сортировка журнала"),
            (self.processing_filter, "Фильтр по статусу обработки"),
            (self.recording_filter, "Фильтр по наличию записи"),
            (self.transcript_filter, "Фильтр по наличию транскрипта"),
            (self.materials_filter, "Фильтр по наличию материалов"),
            (self.detail_payment, "Оплата выбранного занятия"),
            (self.detail_homework, "Домашняя работа выбранного занятия"),
            (self.due_enabled, "Использовать дедлайн домашней работы"),
            (self.due_at, "Дедлайн домашней работы"),
            (self.save_due_button, "Сохранить дедлайн домашней работы"),
            (self.open_lesson_button, "Открыть выбранное занятие"),
            (self.open_materials_button, "Открыть материалы выбранного ученика"),
            (self.open_schedule_button, "Показать выбранное занятие в расписании"),
            (self.more_button, "Показать больше занятий"),
            (self.reset_button, "Очистить фильтры журнала"),
        )
        for widget, name in metadata:
            widget.setAccessibleName(name)
        self.filters_toggle.setAccessibleDescription(
            "Раскрывает оплату, домашнюю работу, статус занятия, время, обработку и ресурсы."
        )
        for button in self._smart_buttons.values():
            button.setAccessibleName(f"Быстрое представление: {button.text()}")
            button.setAccessibleDescription(
                "Переключает журнал на это представление и сохраняет базовые фильтры."
            )

    def _install_tab_order(self) -> None:
        buttons = [
            self._smart_buttons[view]
            for view in (
                JournalSmartView.ALL,
                JournalSmartView.ATTENTION,
                JournalSmartView.UNPAID,
                JournalSmartView.HOMEWORK_REVIEW,
            )
            if view in self._smart_buttons
        ]
        chain: list[QWidget] = [
            *buttons,
            self.search,
            self.student_filter,
            self.subject_filter,
            self.period_filter,
            self.filters_toggle,
            self.table,
            self.detail_payment,
            self.detail_homework,
            self.due_enabled,
            self.due_at,
            self.save_due_button,
            self.open_lesson_button,
            self.open_materials_button,
            self.open_schedule_button,
        ]
        for first, second in zip(chain, chain[1:], strict=False):
            QWidget.setTabOrder(first, second)

    def keyboard_focus_zones(self) -> list[QWidget]:
        smart = self._smart_buttons.get(JournalSmartView.ALL)
        return [
            widget
            for widget in (smart, self.search, self.table, self.detail_payment)
            if widget is not None
        ]

    def focus_search(self) -> None:
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def toggle_advanced_filters(self) -> None:
        self.filters_toggle.setChecked(not self.filters_toggle.isChecked())
        self.filters_toggle.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def handle_escape(self) -> None:
        popup = QApplication.activePopupWidget()
        if popup is not None:
            popup.close()
            self.table.setFocus(Qt.FocusReason.ShortcutFocusReason)
            return
        if self.toast.isVisible():
            self.toast.dismiss()
            self.table.setFocus(Qt.FocusReason.ShortcutFocusReason)
            return
        self.table.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def activate_current_row(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            self._row_activated(row, self.table.currentColumn())

    def toggle_current_payment(self) -> None:
        row_index = self.table.currentRow()
        if not (0 <= row_index < len(self._rows)):
            return
        row = self._rows[row_index]
        if row.lesson.status == "cancelled":
            self.toast.show_message(
                "Отменённое занятие нельзя отметить оплаченным",
                undo_available=False,
            )
            self._position_toast()
            return
        item = self.table.item(row_index, 4)
        if item is None:
            return
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        self.table.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def focus_current_homework(self) -> None:
        row_index = self.table.currentRow()
        if not (0 <= row_index < len(self._rows)):
            return
        combo = self.table.cellWidget(row_index, 5)
        if isinstance(combo, QComboBox) and combo.isEnabled():
            combo.setFocus(Qt.FocusReason.ShortcutFocusReason)

    @staticmethod
    def _attention_reasons(row: LessonJournalRow, now: datetime) -> list[str]:
        lesson = row.lesson
        reasons: list[str] = []
        if lesson.status != "cancelled" and lesson.ends_at < now and not lesson.paid:
            reasons.append("есть задолженность")
        if lesson.ends_at < now and lesson.status == "planned":
            reasons.append("статус занятия не обновлён")
        homework = row.homework
        if homework and homework.received_at and not homework.checked_at:
            reasons.append("домашняя работа ожидает проверки")
        if (
            homework
            and homework.due_at
            and homework.due_at < now
            and row.homework_status
            in {HomeworkStatus.NONE, HomeworkStatus.ASSIGNED, HomeworkStatus.SENT}
        ):
            reasons.append("домашняя работа просрочена")
        if row.processing_status in {"failed", "compile_failed"}:
            reasons.append("ошибка обработки материалов")
        return reasons

    @classmethod
    def _status_descriptor(
        cls,
        row: LessonJournalRow,
        now: datetime,
    ) -> JournalStatusDescriptor:
        lesson = row.lesson
        status = LESSON_STATUS_LABELS.get(lesson.status, lesson.status)
        tone = _STATUS_TONES.get(lesson.status, JournalTone.NEUTRAL)
        reasons = cls._attention_reasons(row, now)
        attention_text = ""
        attention_tone = JournalTone.WARNING
        if reasons:
            attention_text = (
                "Долг"
                if reasons == ["есть задолженность"]
                else "Требует внимания"
            )
            if any("ошибка" in reason for reason in reasons):
                attention_tone = JournalTone.ERROR
        accessible = f"Статус занятия: {status}."
        if reasons:
            accessible += " Требует внимания: " + "; ".join(reasons) + "."
        return JournalStatusDescriptor(
            text=status,
            tone=tone,
            accessible_text=accessible,
            attention_text=attention_text,
            attention_tone=attention_tone,
        )

    def _apply_accessible_row_metadata(self) -> None:
        now = datetime.now()
        for row_index, row in enumerate(self._rows):
            lesson = row.lesson
            status_item = self.table.item(row_index, 3)
            if status_item is not None:
                descriptor = self._status_descriptor(row, now)
                status_item.setText(descriptor.text)
                status_item.setData(STATUS_TEXT_ROLE, descriptor.text)
                status_item.setData(STATUS_TONE_ROLE, descriptor.tone.value)
                status_item.setData(ATTENTION_TEXT_ROLE, descriptor.attention_text)
                status_item.setData(ATTENTION_TONE_ROLE, descriptor.attention_tone.value)
                status_item.setData(
                    Qt.ItemDataRole.AccessibleTextRole,
                    descriptor.accessible_text,
                )
                status_item.setToolTip(descriptor.accessible_text)

            payment = self.table.item(row_index, 4)
            if payment is not None:
                amount = (
                    f"{lesson.rate_cents / 100:,.0f} рублей"
                    if lesson.rate_cents
                    else "без ставки"
                )
                payment_text = "Оплачено" if lesson.paid else "Не оплачено"
                if not lesson.paid and lesson.status != "cancelled" and lesson.ends_at < now:
                    payment_text += ". Имеется задолженность"
                payment.setData(
                    Qt.ItemDataRole.AccessibleTextRole,
                    f"{payment_text}. {amount}. Нажмите пробел для изменения статуса оплаты.",
                )

            resources = self.table.item(row_index, 6)
            if resources is not None:
                description = (
                    f"Запись: {'есть' if row.recording_exists else 'отсутствует'}. "
                    f"Транскрипт: {'есть' if row.transcript_exists else 'отсутствует'}. "
                    f"Материалы: {'есть' if row.materials_exist else 'отсутствуют'}."
                )
                resources.setData(Qt.ItemDataRole.AccessibleTextRole, description)

            lesson_item = self.table.item(row_index, 2)
            if lesson_item is not None:
                lesson_item.setData(
                    Qt.ItemDataRole.AccessibleTextRole,
                    f"{lesson_item.text()}. Ученик: {lesson.student_name}.",
                )
            homework = self.table.cellWidget(row_index, 5)
            if isinstance(homework, QComboBox):
                homework.setAccessibleDescription(
                    f"Текущий статус: {homework.currentText()}. "
                    "F2 из таблицы переводит фокус к этому полю."
                )

    def _empty_action_requested(self, action: str) -> None:
        if action == "clear_filters":
            self.reset_filters()
        elif action == "smart_all":
            self.apply_smart_view("all")
        elif action == "schedule":
            self.show_in_schedule_requested.emit(datetime.now())

    def _update_empty_state(self) -> None:
        if self._rows:
            self.table_stack.setCurrentWidget(self.table)
            return
        if self.current_view == JournalSmartView.ATTENTION:
            self.empty_state.configure(
                title="Сейчас всё в порядке",
                description="В текущей выборке нет занятий, требующих внимания.",
                secondary_text="Все занятия",
                secondary_action="smart_all",
            )
        elif self.current_view == JournalSmartView.UNPAID:
            self.empty_state.configure(
                title="Неоплаченных занятий нет",
                description="Все занятия в текущей выборке отмечены как оплаченные.",
                secondary_text="Все занятия",
                secondary_action="smart_all",
            )
        elif self.current_view == JournalSmartView.HOMEWORK_REVIEW:
            self.empty_state.configure(
                title="ДЗ на проверку нет",
                description="Сейчас нет полученных работ, ожидающих проверки.",
                secondary_text="Все занятия",
                secondary_action="smart_all",
            )
        elif self._has_user_filters():
            self.empty_state.configure(
                title="По выбранным условиям занятий нет",
                description="Измените условия или очистите текущую выборку.",
                primary_text="Очистить фильтры",
                primary_action="clear_filters",
            )
        else:
            period = str(self.period_filter.currentData() or "this_month")
            title = (
                "В этом месяце занятий нет"
                if period == "this_month"
                else "В выбранном периоде занятий нет"
            )
            self.empty_state.configure(
                title=title,
                description="Выберите другой период или откройте расписание.",
                primary_text="Открыть расписание",
                primary_action="schedule",
            )
        self.table_stack.setCurrentWidget(self.empty_state)

    def _render(
        self,
        result: LessonJournalResult,
        *,
        anchor: JournalViewAnchor | None = None,
    ) -> None:
        super()._render(result, anchor=anchor)
        if not self._interaction_ready:
            return
        self._apply_accessible_row_metadata()
        self._update_empty_state()
        self._position_toast()

    def _restore_focus(self, widget: QWidget | None) -> None:
        target = widget if widget is not None and widget.isEnabled() else self.table
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _register_undo(
        self,
        *,
        message: str,
        undo,
        focus_widget: QWidget | None,
    ) -> None:
        self._pending_undo = JournalUndoAction(label=message, undo=undo)
        self._pending_undo_focus = focus_widget
        self.toast.show_message(message, undo_available=True)
        self._position_toast()

    def _apply_reversible_mutation(
        self,
        *,
        message: str,
        action,
        undo,
        anchor: JournalViewAnchor | None,
        focus_widget: QWidget | None,
    ) -> None:
        self._cancel_pending_refresh()
        try:
            action()
        except Exception as exc:
            self.toast.show_message(str(exc), undo_available=False)
            self._position_toast()
            self.refresh(preserve_context=anchor is not None, anchor=anchor)
            self._restore_focus(focus_widget)
            return
        self._register_undo(message=message, undo=undo, focus_widget=focus_widget)
        self.refresh(preserve_context=anchor is not None, anchor=anchor)
        self._restore_focus(focus_widget)

    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 4:
            return
        stored_index = item.data(Qt.ItemDataRole.UserRole)
        row_index = int(stored_index) if stored_index is not None else -1
        if not (0 <= row_index < len(self._rows)):
            return
        row = self._rows[row_index]
        paid = item.checkState() == Qt.CheckState.Checked
        if paid == row.lesson.paid:
            return
        lesson = row.lesson
        previous = lesson.paid
        anchor = self._capture_view_anchor(row_index)
        message = "Оплата отмечена" if paid else "Отметка оплаты снята"
        self._apply_reversible_mutation(
            message=message,
            action=lambda: self.service.set_paid(lesson, paid),
            undo=lambda: self.service.set_paid(lesson, previous),
            anchor=anchor,
            focus_widget=self.table,
        )

    def _homework_changed(self, lesson: ScheduledLesson, combo: QComboBox) -> None:
        if self._loading:
            return
        row_index = int(combo.property("journalRow") or 0)
        if not (0 <= row_index < len(self._rows)):
            return
        current = self._rows[row_index]
        target = HomeworkStatus(str(combo.currentData()))
        if target == current.homework_status:
            return
        snapshot = self.service.snapshot_homework(lesson)
        anchor = self._capture_view_anchor(row_index)
        self._apply_reversible_mutation(
            message=f"ДЗ: {combo.currentText()}",
            action=lambda: self.service.set_homework_status(lesson, target),
            undo=lambda: self.service.restore_homework(lesson, snapshot),
            anchor=anchor,
            focus_widget=self.table,
        )

    def _detail_payment_changed(self, paid: bool) -> None:
        if self._loading_detail:
            return
        row = self._selected_row()
        if row is None or row.lesson.paid == paid:
            return
        lesson = row.lesson
        previous = lesson.paid
        anchor = self._capture_view_anchor()
        self._apply_reversible_mutation(
            message="Оплата отмечена" if paid else "Отметка оплаты снята",
            action=lambda: self.service.set_paid(lesson, paid),
            undo=lambda: self.service.set_paid(lesson, previous),
            anchor=anchor,
            focus_widget=self.detail_payment,
        )

    def _detail_homework_changed(self, _index: int) -> None:
        if self._loading_detail:
            return
        row = self._selected_row()
        if row is None:
            return
        target = HomeworkStatus(str(self.detail_homework.currentData()))
        if target == row.homework_status:
            return
        lesson = row.lesson
        snapshot = self.service.snapshot_homework(lesson)
        anchor = self._capture_view_anchor()
        self._apply_reversible_mutation(
            message=f"ДЗ: {self.detail_homework.currentText()}",
            action=lambda: self.service.set_homework_status(lesson, target),
            undo=lambda: self.service.restore_homework(lesson, snapshot),
            anchor=anchor,
            focus_widget=self.detail_homework,
        )

    def _save_due(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        lesson = row.lesson
        snapshot = self.service.snapshot_homework(lesson)
        due = (
            self._qdatetime_to_datetime(self.due_at.dateTime())
            if self.due_enabled.isChecked()
            else None
        )
        anchor = self._capture_view_anchor()
        message = "Срок ДЗ сохранён" if due is not None else "Срок ДЗ удалён"
        self._apply_reversible_mutation(
            message=message,
            action=lambda: self.service.set_homework_due(lesson, due),
            undo=lambda: self.service.restore_homework(lesson, snapshot),
            anchor=anchor,
            focus_widget=self.save_due_button,
        )

    def undo_last_action(self) -> None:
        pending = self._pending_undo
        if pending is None:
            return
        anchor = self._capture_view_anchor()
        focus = self._pending_undo_focus
        self._pending_undo = None
        self._pending_undo_focus = None
        self.toast.timer.stop()
        try:
            pending.undo()
        except Exception as exc:
            self.toast.show_message(
                f"Не удалось отменить изменение: {exc}",
                undo_available=False,
            )
        else:
            self.refresh(preserve_context=anchor is not None, anchor=anchor)
            self.toast.show_message("Последнее изменение отменено", undo_available=False)
        self._position_toast()
        self._restore_focus(focus)

    def _expire_undo(self) -> None:
        self._pending_undo = None
        self._pending_undo_focus = None

    def _position_toast(self) -> None:
        if not hasattr(self, "toast"):
            return
        width = min(560, max(320, self.width() - 48))
        height = max(48, self.toast.sizeHint().height())
        self.toast.resize(width, height)
        x = max(12, (self.width() - width) // 2)
        y = max(12, self.height() - height - 22)
        self.toast.move(x, y)
        if self.toast.isVisible():
            self.toast.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_toast()
