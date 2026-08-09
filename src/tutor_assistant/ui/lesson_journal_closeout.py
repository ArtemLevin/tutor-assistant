from __future__ import annotations

from dataclasses import fields
from datetime import datetime

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QWidget,
)

from ..lesson_closeout import ATTENDANCE_LABELS, AttendanceStatus
from ..lesson_journal import LessonJournalFilter
from .journal_closeout import (
    CloseoutAwareLessonJournalService,
    CloseoutJournalFilter,
    CloseoutJournalRow,
    CloseoutJournalSummary,
)
from .journal_widgets import ATTENTION_TEXT_ROLE, ATTENTION_TONE_ROLE, JournalTone
from .lesson_journal_interactions import LessonJournalInteractionPage
from .lesson_journal_ux import JournalSmartView, JournalViewAnchor
from .theme import set_button_kind


_ATTENDANCE_TONES = {
    AttendanceStatus.PRESENT: JournalTone.SUCCESS,
    AttendanceStatus.LATE: JournalTone.WARNING,
    AttendanceStatus.NO_SHOW: JournalTone.ERROR,
    AttendanceStatus.EXCUSED: JournalTone.NEUTRAL,
    AttendanceStatus.UNKNOWN: JournalTone.NEUTRAL,
}


class LessonJournalCloseoutPage(LessonJournalInteractionPage):
    """Operational lesson journal with pedagogical closeout workflow."""

    def __init__(self, *args, **kwargs) -> None:
        self._closeout_ready = False
        self._closeout_loading = False
        self._selection_guard = False
        self._unfinished_view_active = False
        self._loaded_identity: tuple[str, datetime] | None = None
        self._loaded_lesson = None
        self._note_baseline = ""
        self._note_dirty = False
        self._closeout_undo_anchor: JournalViewAnchor | None = None
        self._registering_closeout_undo = False
        super().__init__(*args, **kwargs)
        lesson_store = getattr(self.service, "lesson_store", None)
        self.service = CloseoutAwareLessonJournalService(self.store, lesson_store)
        self.closeout_service = self.service.closeout_service
        self._install_closeout_layer()
        self._closeout_ready = True
        self._restore_closeout_state(self._pending_ux_state or {})
        self.refresh(preserve_context=True)

    def _install_closeout_layer(self) -> None:
        self.setStyleSheet(
            self.styleSheet()
            + """
            QTextEdit:focus {
                border: 2px solid #275AA6;
            }
            QLabel#journalDirtyState {
                color: #667085;
                font-size: 12px;
            }
            """
        )
        self._install_unfinished_view()
        self._install_attendance_filter()
        self._install_closeout_summary()
        self._install_closeout_details()
        self._install_closeout_shortcuts()
        self._install_closeout_tab_order()

    def _install_unfinished_view(self) -> None:
        smart_layout = self.layout().itemAt(1).layout()
        self.unfinished_button = QPushButton("Незавершённые")
        self.unfinished_button.setObjectName("journalSmartButton")
        self.unfinished_button.setCheckable(True)
        self.unfinished_button.setAccessibleName(
            "Быстрое представление: Незавершённые занятия"
        )
        self.unfinished_button.setAccessibleDescription(
            "Показывает прошедшие занятия, для которых требуется завершение или посещаемость."
        )
        self.smart_group.addButton(self.unfinished_button)
        insert_at = max(1, smart_layout.count() - 2)
        smart_layout.insertWidget(insert_at, self.unfinished_button)
        self.unfinished_button.clicked.connect(self.apply_unfinished_view)

    def _install_attendance_filter(self) -> None:
        filter_panel = self.layout().itemAt(2).widget()
        filter_layout = filter_panel.layout()
        advanced_row = filter_layout.itemAt(1).layout()
        self.attendance_filter = QComboBox()
        self.attendance_filter.setAccessibleName("Фильтр журнала по посещаемости")
        self.attendance_filter.addItem("Любая посещаемость", "all")
        for status in AttendanceStatus:
            self.attendance_filter.addItem(ATTENDANCE_LABELS[status], status.value)
        advanced_row.insertWidget(3, self.attendance_filter)
        self._advanced_widgets = (*self._advanced_widgets, self.attendance_filter)
        self.attendance_filter.setVisible(self.filters_toggle.isChecked())
        self.attendance_filter.currentIndexChanged.connect(self._schedule_refresh)

    def _install_closeout_summary(self) -> None:
        summary_layout = self.layout().itemAt(4).layout()
        self.summary_unfinished = QLabel()
        self.summary_unfinished.setObjectName("journalSummaryPill")
        self.summary_unfinished.setProperty("tone", "warning")
        self.summary_unfinished.setAccessibleName("Незавершённые занятия")
        summary_layout.insertWidget(max(0, summary_layout.count() - 1), self.summary_unfinished)
        self.summary_unfinished.hide()

    def _install_closeout_details(self) -> None:
        details = self.detail_payment.parentWidget()
        details.setMinimumWidth(340)
        details.setMaximumWidth(460)
        layout = details.layout()
        payment_index = layout.indexOf(self.detail_payment)

        self.attendance_label = QLabel("Посещаемость")
        self.attendance_label.setObjectName("muted")
        layout.insertWidget(payment_index, self.attendance_label)
        payment_index += 1

        self.detail_attendance = QComboBox()
        self.detail_attendance.setAccessibleName("Посещаемость выбранного занятия")
        self.detail_attendance.setAccessibleDescription(
            "F3 переводит фокус к посещаемости выбранного занятия."
        )
        for status in AttendanceStatus:
            self.detail_attendance.addItem(ATTENDANCE_LABELS[status], status.value)
        self.detail_attendance.setEnabled(False)
        self.detail_attendance.currentIndexChanged.connect(self._attendance_changed)
        layout.insertWidget(payment_index, self.detail_attendance)
        payment_index += 1

        self.note_label = QLabel("Итог занятия")
        self.note_label.setObjectName("muted")
        layout.insertWidget(payment_index, self.note_label)
        payment_index += 1

        self.teacher_note = QTextEdit()
        self.teacher_note.setPlaceholderText(
            "Что прошли, что вызвало трудности, на что обратить внимание дальше"
        )
        self.teacher_note.setAccessibleName("Педагогическая заметка по занятию")
        self.teacher_note.setAccessibleDescription(
            "Локальная заметка преподавателя. Ctrl+S сохраняет черновик."
        )
        self.teacher_note.setMinimumHeight(90)
        self.teacher_note.setMaximumHeight(150)
        self.teacher_note.setEnabled(False)
        self.teacher_note.textChanged.connect(self._note_changed)
        layout.insertWidget(payment_index, self.teacher_note)
        payment_index += 1

        self.note_state = QLabel("Все изменения сохранены")
        self.note_state.setObjectName("journalDirtyState")
        self.note_state.setAccessibleName("Состояние педагогической заметки")
        layout.insertWidget(payment_index, self.note_state)
        payment_index += 1

        self.save_closeout_button = set_button_kind(
            QPushButton("Сохранить итог"),
            "ghost",
        )
        self.save_closeout_button.setAccessibleName("Сохранить итог выбранного занятия")
        self.save_closeout_button.setEnabled(False)
        self.save_closeout_button.clicked.connect(self.save_closeout_draft)
        layout.insertWidget(payment_index, self.save_closeout_button)

        processing_index = layout.indexOf(self.detail_processing)
        self.close_lesson_button = set_button_kind(
            QPushButton("Завершить занятие"),
            "primary",
        )
        self.close_lesson_button.setAccessibleName("Завершить выбранное занятие")
        self.close_lesson_button.setAccessibleDescription(
            "Сохраняет посещаемость и педагогический итог и переводит занятие в завершённое."
        )
        self.close_lesson_button.setEnabled(False)
        self.close_lesson_button.clicked.connect(self.close_current_lesson)
        layout.insertWidget(processing_index, self.close_lesson_button)

    def _install_closeout_shortcuts(self) -> None:
        self.closeout_shortcuts: list[QShortcut] = []
        for sequence, callback in (
            ("Ctrl+S", self.save_closeout_draft),
            ("Ctrl+Enter", self.close_current_lesson),
            ("F3", self.focus_attendance),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self.closeout_shortcuts.append(shortcut)

    def _install_closeout_tab_order(self) -> None:
        chain: list[QWidget] = [
            self.table,
            self.detail_attendance,
            self.teacher_note,
            self.save_closeout_button,
            self.detail_payment,
            self.detail_homework,
            self.due_enabled,
            self.due_at,
            self.save_due_button,
            self.close_lesson_button,
            self.open_lesson_button,
            self.open_materials_button,
            self.open_schedule_button,
        ]
        for first, second in zip(chain, chain[1:], strict=False):
            QWidget.setTabOrder(first, second)

    def _restore_closeout_state(self, state: dict[str, object]) -> None:
        if hasattr(self, "attendance_filter"):
            value = str(state.get("attendance", "all") or "all")
            index = self.attendance_filter.findData(value)
            blocker = QSignalBlocker(self.attendance_filter)
            self.attendance_filter.setCurrentIndex(max(0, index))
            del blocker
        if str(state.get("closeout_view", "")) == "unfinished":
            self._unfinished_view_active = True
            self.current_view = JournalSmartView.ALL
            self.unfinished_button.setChecked(True)

    def _filters(self) -> CloseoutJournalFilter:
        base = super()._filters()
        values = {
            field.name: getattr(base, field.name)
            for field in fields(LessonJournalFilter)
        }
        attendance = (
            str(self.attendance_filter.currentData() or "all")
            if hasattr(self, "attendance_filter")
            else "all"
        )
        return CloseoutJournalFilter(
            **values,
            attendance=attendance,
            unfinished_only=bool(getattr(self, "_unfinished_view_active", False)),
        )

    def apply_unfinished_view(self) -> None:
        self._unfinished_view_active = True
        self.current_view = JournalSmartView.ALL
        self.unfinished_button.setChecked(True)
        self._visible_limit = self.page_size
        self._update_filter_ui()
        self.refresh()

    def apply_smart_view(self, view: str) -> None:
        self._unfinished_view_active = False
        super().apply_smart_view(view)

    def _advanced_filter_count(self) -> int:
        count = super()._advanced_filter_count()
        if hasattr(self, "attendance_filter"):
            count += str(self.attendance_filter.currentData() or "all") != "all"
        return count

    def _has_user_filters(self) -> bool:
        return super()._has_user_filters() or bool(
            getattr(self, "_unfinished_view_active", False)
        )

    def _rebuild_filter_chips(self) -> None:
        super()._rebuild_filter_chips()
        if not hasattr(self, "attendance_filter"):
            return
        if str(self.attendance_filter.currentData() or "all") == "all":
            return
        if self.chips_layout.count():
            last = self.chips_layout.itemAt(self.chips_layout.count() - 1)
            if last.spacerItem() is not None:
                self.chips_layout.takeAt(self.chips_layout.count() - 1)
        self._add_filter_chip(
            f"Посещаемость: {self.attendance_filter.currentText()}",
            lambda: self.attendance_filter.setCurrentIndex(0),
        )
        self.chips_layout.addStretch(1)
        self.chips_widget.setVisible(True)

    def reset_filters(self) -> None:
        self._unfinished_view_active = False
        if hasattr(self, "attendance_filter"):
            blocker = QSignalBlocker(self.attendance_filter)
            self.attendance_filter.setCurrentIndex(0)
            del blocker
        super().reset_filters()

    def filter_state(self) -> dict[str, object]:
        state = super().filter_state()
        state["attendance"] = (
            str(self.attendance_filter.currentData() or "all")
            if hasattr(self, "attendance_filter")
            else "all"
        )
        state["closeout_view"] = (
            "unfinished" if getattr(self, "_unfinished_view_active", False) else ""
        )
        return state

    def restore_filter_state(self, state: dict[str, object]) -> None:
        super().restore_filter_state(state)
        if not getattr(self, "_closeout_ready", False):
            return
        self._restore_closeout_state(state)
        self._update_filter_ui()

    @staticmethod
    def _identity(row) -> tuple[str, datetime]:
        return row.lesson.student_id, row.lesson.starts_at

    def _note_changed(self) -> None:
        if self._closeout_loading:
            return
        self._note_dirty = self.teacher_note.toPlainText() != self._note_baseline
        self._update_note_state()

    def _update_note_state(self) -> None:
        if not hasattr(self, "note_state"):
            return
        text = (
            "Есть несохранённые изменения"
            if self._note_dirty
            else "Все изменения сохранены"
        )
        self.note_state.setText(text)
        self.note_state.setAccessibleDescription(text)
        self.save_closeout_button.setEnabled(
            bool(self._loaded_lesson)
            and getattr(self._loaded_lesson, "status", "") != "cancelled"
            and self._note_dirty
        )

    def _set_note_clean(self, note: str) -> None:
        self._note_baseline = note
        self._note_dirty = False
        self._update_note_state()

    def _confirm_dirty_transition(self) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("Несохранённый итог занятия")
        box.setText("В педагогической заметке есть несохранённые изменения.")
        save = box.addButton("Сохранить", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(
            "Отменить изменения",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        stay = box.addButton("Остаться", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return "save"
        if clicked is discard:
            return "discard"
        if clicked is stay:
            return "stay"
        return "stay"

    def _restore_loaded_selection(self) -> None:
        if self._loaded_identity is None:
            return
        for index, row in enumerate(self._rows):
            if self._identity(row) == self._loaded_identity:
                blocker = QSignalBlocker(self.table)
                self.table.selectRow(index)
                del blocker
                return

    def _save_loaded_draft(self) -> bool:
        if self._loaded_lesson is None:
            return True
        try:
            self.closeout_service.save_draft(
                self._loaded_lesson,
                attendance=AttendanceStatus(str(self.detail_attendance.currentData())),
                teacher_note=self.teacher_note.toPlainText(),
            )
        except Exception as exc:
            self.toast.show_message(str(exc), undo_available=False)
            self._position_toast()
            return False
        self._set_note_clean(self.teacher_note.toPlainText())
        return True

    def _selection_changed(self) -> None:
        if not getattr(self, "_closeout_ready", False) or self._selection_guard:
            super()._selection_changed()
            return
        current = self._selected_row()
        new_identity = self._identity(current) if current is not None else None
        if (
            self._loaded_identity is not None
            and new_identity != self._loaded_identity
            and self._note_dirty
        ):
            choice = self._confirm_dirty_transition()
            if choice == "stay":
                self._restore_loaded_selection()
                return
            if choice == "save" and not self._save_loaded_draft():
                self._restore_loaded_selection()
                return
            if choice == "discard":
                self._note_dirty = False
        super()._selection_changed()
        current = self._selected_row()
        if current is None:
            self._clear_closeout_details()
            return
        identity = self._identity(current)
        if identity == self._loaded_identity and self._note_dirty:
            return
        self._load_closeout_details(current)

    def _load_closeout_details(self, row) -> None:
        lesson = row.lesson
        meta = row.closeout if isinstance(row, CloseoutJournalRow) else None
        self._closeout_loading = True
        try:
            self._loaded_identity = self._identity(row)
            self._loaded_lesson = lesson
            attendance = meta.attendance if meta is not None else AttendanceStatus.UNKNOWN
            blocker = QSignalBlocker(self.detail_attendance)
            self.detail_attendance.setCurrentIndex(
                max(0, self.detail_attendance.findData(attendance.value))
            )
            del blocker
            note = meta.teacher_note if meta is not None else ""
            note_blocker = QSignalBlocker(self.teacher_note)
            self.teacher_note.setPlainText(note)
            del note_blocker
            self._note_baseline = note
            self._note_dirty = False
            editable = lesson.status != "cancelled"
            self.detail_attendance.setEnabled(editable)
            self.teacher_note.setEnabled(editable)
            self._update_note_state()
            closed = bool(
                meta
                and meta.closed_at is not None
                and lesson.status == "completed"
            )
            if closed:
                self.close_lesson_button.setText("Занятие завершено")
                self.close_lesson_button.setEnabled(False)
                self.close_lesson_button.setAccessibleDescription(
                    f"Занятие завершено {meta.closed_at:%d.%m.%Y в %H:%M}."
                )
            else:
                self.close_lesson_button.setText("Завершить занятие")
                can_close = editable and lesson.ends_at <= datetime.now()
                self.close_lesson_button.setEnabled(can_close)
                self.close_lesson_button.setAccessibleDescription(
                    "Сохраняет посещаемость и педагогический итог и переводит занятие в завершённое."
                    if can_close
                    else "Завершение станет доступно после окончания занятия."
                )
        finally:
            self._closeout_loading = False

    def _clear_closeout_details(self) -> None:
        if not hasattr(self, "detail_attendance"):
            return
        self._closeout_loading = True
        try:
            self._loaded_identity = None
            self._loaded_lesson = None
            self._note_baseline = ""
            self._note_dirty = False
            self.detail_attendance.setCurrentIndex(0)
            self.detail_attendance.setEnabled(False)
            self.teacher_note.clear()
            self.teacher_note.setEnabled(False)
            self.save_closeout_button.setEnabled(False)
            self.close_lesson_button.setText("Завершить занятие")
            self.close_lesson_button.setEnabled(False)
            self._update_note_state()
        finally:
            self._closeout_loading = False

    def _clear_details(self) -> None:
        super()._clear_details()
        if getattr(self, "_closeout_ready", False):
            self._clear_closeout_details()

    def _attendance_changed(self, _index: int) -> None:
        if self._closeout_loading or not self._closeout_ready:
            return
        row = self._selected_row()
        if row is None or row.lesson.status == "cancelled":
            return
        target = AttendanceStatus(str(self.detail_attendance.currentData()))
        current = (
            row.attendance
            if isinstance(row, CloseoutJournalRow)
            else AttendanceStatus.UNKNOWN
        )
        if target == current:
            return
        lesson = row.lesson
        snapshot = self.closeout_service.snapshot(lesson)
        anchor = self._capture_view_anchor()
        self._apply_reversible_mutation(
            message=f"Посещаемость: {ATTENDANCE_LABELS[target]}",
            action=lambda: self.closeout_service.set_attendance(lesson, target),
            undo=lambda: self.closeout_service.restore_snapshot(lesson, snapshot),
            anchor=anchor,
            focus_widget=self.detail_attendance,
        )

    def save_closeout_draft(self) -> None:
        row = self._selected_row()
        if row is None or row.lesson.status == "cancelled":
            return
        anchor = self._capture_view_anchor()
        try:
            self.closeout_service.save_draft(
                row.lesson,
                attendance=AttendanceStatus(str(self.detail_attendance.currentData())),
                teacher_note=self.teacher_note.toPlainText(),
            )
        except Exception as exc:
            self.toast.show_message(str(exc), undo_available=False)
            self._position_toast()
            return
        self._set_note_clean(self.teacher_note.toPlainText())
        self.toast.show_message("Итог занятия сохранён", undo_available=False)
        self.refresh(preserve_context=anchor is not None, anchor=anchor)
        self._position_toast()
        self.teacher_note.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def close_current_lesson(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        lesson = row.lesson
        attendance = AttendanceStatus(str(self.detail_attendance.currentData()))
        if attendance == AttendanceStatus.UNKNOWN:
            self.toast.show_message(
                "Укажите посещаемость перед завершением занятия",
                undo_available=False,
            )
            self._position_toast()
            self.detail_attendance.setFocus(Qt.FocusReason.ShortcutFocusReason)
            return
        snapshot = self.closeout_service.snapshot(lesson)
        anchor = self._capture_view_anchor()
        note = self.teacher_note.toPlainText()
        self._cancel_pending_refresh()
        try:
            self.closeout_service.close_lesson(
                lesson,
                attendance=attendance,
                teacher_note=note,
            )
        except Exception as exc:
            self.toast.show_message(str(exc), undo_available=False)
            self._position_toast()
            return
        self._set_note_clean(note)
        self._registering_closeout_undo = True
        try:
            self._register_undo(
                message=f"Занятие завершено: {lesson.student_name}",
                undo=lambda: self.closeout_service.restore_snapshot(lesson, snapshot),
                focus_widget=self.table,
            )
        finally:
            self._registering_closeout_undo = False
        self._closeout_undo_anchor = anchor
        self.refresh(preserve_context=anchor is not None, anchor=anchor)
        self.table.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _register_undo(self, *, message: str, undo, focus_widget: QWidget | None) -> None:
        if not self._registering_closeout_undo:
            self._closeout_undo_anchor = None
        super()._register_undo(message=message, undo=undo, focus_widget=focus_widget)

    def undo_last_action(self) -> None:
        if self._closeout_undo_anchor is None:
            super().undo_last_action()
            return
        pending = self._pending_undo
        if pending is None:
            self._closeout_undo_anchor = None
            return
        anchor = self._closeout_undo_anchor
        focus = self._pending_undo_focus
        self._pending_undo = None
        self._pending_undo_focus = None
        self._closeout_undo_anchor = None
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
            self.toast.show_message("Завершение занятия отменено", undo_available=False)
        self._position_toast()
        self._restore_focus(focus)

    def _expire_undo(self) -> None:
        self._closeout_undo_anchor = None
        super()._expire_undo()

    def focus_attendance(self) -> None:
        if self.detail_attendance.isEnabled():
            self.detail_attendance.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def keyboard_focus_zones(self) -> list[QWidget]:
        zones = super().keyboard_focus_zones()
        if hasattr(self, "detail_attendance"):
            zones.append(self.detail_attendance)
        return zones

    def _apply_accessible_row_metadata(self) -> None:
        super()._apply_accessible_row_metadata()
        if not getattr(self, "_closeout_ready", False):
            return
        for row_index, row in enumerate(self._rows):
            if not isinstance(row, CloseoutJournalRow):
                continue
            if row.attendance == AttendanceStatus.UNKNOWN:
                continue
            status_item = self.table.item(row_index, 3)
            if status_item is None:
                continue
            attendance_text = ATTENDANCE_LABELS[row.attendance]
            status_item.setData(ATTENTION_TEXT_ROLE, attendance_text)
            status_item.setData(
                ATTENTION_TONE_ROLE,
                _ATTENDANCE_TONES[row.attendance].value,
            )
            existing = str(
                status_item.data(Qt.ItemDataRole.AccessibleTextRole) or status_item.text()
            )
            accessible = f"{existing} Посещаемость: {attendance_text}."
            status_item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible)
            status_item.setToolTip(accessible)

    def _update_empty_state(self) -> None:
        if getattr(self, "_unfinished_view_active", False) and not self._rows:
            self.empty_state.configure(
                title="Все прошедшие занятия обработаны",
                description="В текущей выборке нет занятий, ожидающих завершения.",
                secondary_text="Все занятия",
                secondary_action="smart_all",
            )
            self.table_stack.setCurrentWidget(self.empty_state)
            return
        super()._update_empty_state()

    def _empty_action_requested(self, action: str) -> None:
        if action == "smart_all":
            self._unfinished_view_active = False
        super()._empty_action_requested(action)

    def _render(
        self,
        result,
        *,
        anchor: JournalViewAnchor | None = None,
    ) -> None:
        super()._render(result, anchor=anchor)
        if not getattr(self, "_closeout_ready", False):
            return
        summary = result.summary
        unfinished = summary.unfinished if isinstance(summary, CloseoutJournalSummary) else 0
        self.summary_unfinished.setText(f"{unfinished} незавершённых")
        self.summary_unfinished.setVisible(unfinished > 0)
