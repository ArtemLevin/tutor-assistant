from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QDate, QSignalBlocker, QTime, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget

from ..lesson_closeout import ATTENDANCE_LABELS, AttendanceStatus, LessonCloseoutMeta
from .journal_closeout import CloseoutJournalRow
from .journal_widgets import (
    ATTENDANCE_TEXT_ROLE,
    ATTENDANCE_TONE_ROLE,
    JournalTone,
    announce_accessible,
)
from .lesson_journal_closeout import LessonJournalCloseoutPage
from .lesson_journal_interactions import LessonJournalInteractionPage
from .lesson_journal_ux import JournalSmartView


_ATTENDANCE_TONES = {
    AttendanceStatus.PRESENT: JournalTone.SUCCESS,
    AttendanceStatus.LATE: JournalTone.WARNING,
    AttendanceStatus.NO_SHOW: JournalTone.ERROR,
    AttendanceStatus.EXCUSED: JournalTone.NEUTRAL,
    AttendanceStatus.UNKNOWN: JournalTone.NEUTRAL,
}


class LessonJournalCloseoutStablePage(LessonJournalCloseoutPage):
    """Production closeout page with data-loss, Undo and interaction hardening."""

    def __init__(self, *args, **kwargs) -> None:
        self._hardening_ready = False
        self._attendance_baseline = AttendanceStatus.UNKNOWN
        self._loaded_closeout_state: LessonCloseoutMeta | None = None
        self._last_applied_filter_state: dict[str, object] | None = None
        self._last_announced_dirty: bool | None = None
        super().__init__(*args, **kwargs)
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.close_current_lesson)
        self.closeout_shortcuts.append(shortcut)
        self.note_state.setAccessibleName("Состояние итога занятия")
        self.detail_attendance.setAccessibleDescription(
            "Посещаемость сохраняется вместе с итогом занятия. F3 переводит фокус к этому полю."
        )
        self.save_closeout_button.setAccessibleDescription(
            "Сохраняет посещаемость и педагогическую заметку как черновик итога занятия."
        )
        self._install_hardened_tab_order()
        self._hardening_ready = True
        self._sync_closeout_details()
        self._last_applied_filter_state = self.filter_state()
        self._update_note_state()

    def _draft_values_dirty(self) -> bool:
        if self._loaded_lesson is None or not hasattr(self, "detail_attendance"):
            return False
        try:
            attendance = AttendanceStatus(str(self.detail_attendance.currentData()))
        except ValueError:
            attendance = AttendanceStatus.UNKNOWN
        return bool(
            self.teacher_note.toPlainText() != self._note_baseline
            or attendance != self._attendance_baseline
        )

    def _note_changed(self) -> None:
        if self._closeout_loading:
            return
        self._note_dirty = self._draft_values_dirty()
        self._update_note_state()

    def _attendance_changed(self, _index: int) -> None:
        if self._closeout_loading or not getattr(self, "_closeout_ready", False):
            return
        row = self._selected_row()
        if row is None or row.lesson.status == "cancelled":
            return
        try:
            target = AttendanceStatus(str(self.detail_attendance.currentData()))
        except ValueError:
            target = AttendanceStatus.UNKNOWN
        self._set_transient_attendance(target)
        self._note_dirty = self._draft_values_dirty()
        self._update_note_state()

    def _set_transient_attendance(self, attendance: AttendanceStatus) -> None:
        row_index = self.table.currentRow()
        if not (0 <= row_index < len(self._rows)):
            return
        row = self._rows[row_index]
        if not isinstance(row, CloseoutJournalRow):
            return
        current = row.closeout
        if current is None:
            current = LessonCloseoutMeta(
                occurrence_id=row.lesson.occurrence_id or -1,
                attendance=attendance,
            )
        else:
            current = replace(current, attendance=attendance)
        self._rows[row_index] = replace(row, closeout=current)
        self._apply_accessible_row_metadata()
        self.table.viewport().update()

    def _update_note_state(self) -> None:
        if not hasattr(self, "note_state"):
            return
        dirty = bool(self._note_dirty)
        text = "Есть несохранённые изменения" if dirty else "Все изменения сохранены"
        self.note_state.setText(text)
        self.note_state.setAccessibleDescription(text)
        self.save_closeout_button.setEnabled(
            bool(self._loaded_lesson)
            and getattr(self._loaded_lesson, "status", "") != "cancelled"
            and dirty
        )
        if (
            getattr(self, "_hardening_ready", False)
            and not self._closeout_loading
            and self._last_announced_dirty is not dirty
        ):
            announce_accessible(self.note_state, text, assertive=dirty)
        self._last_announced_dirty = dirty

    def _set_note_clean(self, note: str) -> None:
        self._note_baseline = note
        try:
            self._attendance_baseline = AttendanceStatus(
                str(self.detail_attendance.currentData())
            )
        except (AttributeError, ValueError):
            self._attendance_baseline = AttendanceStatus.UNKNOWN
        self._note_dirty = False
        self._update_note_state()

    def _confirm_dirty_transition(self) -> str:
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle("Несохранённый итог занятия")
        box.setText("В итоге занятия есть несохранённые изменения.")
        save = box.addButton("Сохранить", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(
            "Отменить изменения",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        box.addButton("Остаться", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return "save"
        if clicked is discard:
            return "discard"
        return "stay"

    def _discard_closeout_draft(self) -> None:
        self._closeout_loading = True
        try:
            attendance_blocker = QSignalBlocker(self.detail_attendance)
            self.detail_attendance.setCurrentIndex(
                max(0, self.detail_attendance.findData(self._attendance_baseline.value))
            )
            del attendance_blocker
            note_blocker = QSignalBlocker(self.teacher_note)
            self.teacher_note.setPlainText(self._note_baseline)
            del note_blocker
            row_index = self.table.currentRow()
            if 0 <= row_index < len(self._rows):
                row = self._rows[row_index]
                if isinstance(row, CloseoutJournalRow):
                    self._rows[row_index] = replace(
                        row,
                        closeout=self._loaded_closeout_state,
                    )
            self._note_dirty = False
        finally:
            self._closeout_loading = False
        self._apply_accessible_row_metadata()
        self.table.viewport().update()
        self._update_note_state()

    def _resolve_dirty_before_context_change(self) -> bool:
        if not self._note_dirty:
            return True
        choice = self._confirm_dirty_transition()
        if choice == "save":
            return self._save_loaded_draft()
        if choice == "discard":
            self._discard_closeout_draft()
            return True
        return False

    def _load_closeout_details(self, row) -> None:
        super()._load_closeout_details(row)
        if not getattr(self, "_closeout_ready", False):
            return
        meta = self.closeout_service.get_for_lesson(row.lesson)
        attendance = meta.attendance if meta is not None else AttendanceStatus.UNKNOWN
        note = meta.teacher_note if meta is not None else ""
        self._closeout_loading = True
        try:
            attendance_blocker = QSignalBlocker(self.detail_attendance)
            self.detail_attendance.setCurrentIndex(
                max(0, self.detail_attendance.findData(attendance.value))
            )
            del attendance_blocker
            note_blocker = QSignalBlocker(self.teacher_note)
            self.teacher_note.setPlainText(note)
            del note_blocker
            self._attendance_baseline = attendance
            self._note_baseline = note
            self._loaded_closeout_state = meta
            row_index = self.table.currentRow()
            if 0 <= row_index < len(self._rows) and isinstance(row, CloseoutJournalRow):
                self._rows[row_index] = replace(row, closeout=meta)
            self._note_dirty = False
        finally:
            self._closeout_loading = False
        self._update_note_state()

    def _clear_closeout_details(self) -> None:
        super()._clear_closeout_details()
        self._attendance_baseline = AttendanceStatus.UNKNOWN
        self._loaded_closeout_state = None
        self._note_dirty = False

    def _sync_closeout_details(self) -> None:
        if not getattr(self, "_closeout_ready", False):
            return
        if not self._rows:
            self._clear_closeout_details()
            return
        row = self._selected_row()
        if row is None:
            self.table.selectRow(0)
            row = self._selected_row()
        if row is None:
            self._clear_closeout_details()
            return
        identity = self._identity(row)
        if identity == self._loaded_identity and self._note_dirty:
            return
        self._load_closeout_details(row)

    def _restore_filter_controls(self, state: dict[str, object]) -> None:
        self._loading = True
        try:
            self.search.setText(str(state.get("query", "")))
            for combo, key, default in (
                (self.student_filter, "student", ""),
                (self.subject_filter, "subject", ""),
                (self.period_filter, "period", "this_month"),
                (self.payment_filter, "payment", "all"),
                (self.homework_filter, "homework", "all"),
                (self.status_filter, "status", ""),
                (self.processing_filter, "processing", ""),
                (self.recording_filter, "recording", ""),
                (self.transcript_filter, "transcript", ""),
                (self.materials_filter, "materials", ""),
                (self.sort_filter, "sort", "date_desc"),
                (self.attendance_filter, "attendance", "all"),
            ):
                self._restore_combo(combo, state.get(key, default))
            self.attention_only.setChecked(bool(state.get("attention", False)))
            self.time_enabled.setChecked(bool(state.get("time_enabled", False)))
            for editor, key in ((self.date_from, "date_from"), (self.date_to, "date_to")):
                parsed = QDate.fromString(str(state.get(key, "")), "yyyy-MM-dd")
                if parsed.isValid():
                    editor.setDate(parsed)
            for editor, key in ((self.time_from, "time_from"), (self.time_to, "time_to")):
                parsed = QTime.fromString(str(state.get(key, "")), "HH:mm")
                if parsed.isValid():
                    editor.setTime(parsed)
            try:
                view = JournalSmartView(str(state.get("smart_view", "all")))
            except ValueError:
                view = JournalSmartView.ALL
            unfinished = str(state.get("closeout_view", "")) == "unfinished"
            self._unfinished_view_active = unfinished
            if unfinished:
                self.current_view = JournalSmartView.ALL
                self.unfinished_button.setChecked(True)
            else:
                self._set_smart_view(view)
            expanded = bool(state.get("advanced_expanded", False))
            blocker = QSignalBlocker(self.filters_toggle)
            self.filters_toggle.setChecked(expanded)
            del blocker
            self._set_advanced_visible(expanded)
            custom = self.period_filter.currentData() == "custom"
            self.date_from.setEnabled(custom)
            self.date_to.setEnabled(custom)
            self.date_from.setVisible(custom)
            self.date_to.setVisible(custom)
            self.time_from.setEnabled(self.time_enabled.isChecked())
            self.time_to.setEnabled(self.time_enabled.isChecked())
        finally:
            self._loading = False
        self._update_filter_ui()

    def refresh(self, *, preserve_context: bool = False, anchor=None) -> None:
        if not getattr(self, "_hardening_ready", False):
            super().refresh(preserve_context=preserve_context, anchor=anchor)
            return
        if self._loading:
            return
        current_state = self.filter_state()
        if self._note_dirty:
            if self._last_applied_filter_state == current_state:
                return
            if not self._resolve_dirty_before_context_change():
                if self._last_applied_filter_state is not None:
                    self._restore_filter_controls(self._last_applied_filter_state)
                self._restore_loaded_selection()
                return
        super().refresh(preserve_context=preserve_context, anchor=anchor)
        self._last_applied_filter_state = self.filter_state()

    def _apply_reversible_mutation(self, **kwargs) -> None:
        if getattr(self, "_hardening_ready", False) and self._note_dirty:
            focus_widget = kwargs.get("focus_widget")
            if not self._resolve_dirty_before_context_change():
                self._restore_focus(focus_widget)
                return
        super()._apply_reversible_mutation(**kwargs)

    def _show_more(self) -> None:
        if self._note_dirty and not self._resolve_dirty_before_context_change():
            return
        super()._show_more()

    def save_closeout_draft(self) -> None:
        if self._loaded_lesson is None:
            return
        self._expire_undo()
        super().save_closeout_draft()

    def close_current_lesson(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        lesson = row.lesson
        try:
            attendance = AttendanceStatus(str(self.detail_attendance.currentData()))
        except ValueError:
            attendance = AttendanceStatus.UNKNOWN
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
                undo=lambda: self.closeout_service.reopen_with_current_draft(
                    lesson,
                    snapshot,
                    attendance=attendance,
                    teacher_note=note,
                ),
                focus_widget=self.table,
            )
        finally:
            self._registering_closeout_undo = False
        self._closeout_undo_anchor = anchor
        self.refresh(preserve_context=anchor is not None, anchor=anchor)
        self.table.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _apply_accessible_row_metadata(self) -> None:
        LessonJournalInteractionPage._apply_accessible_row_metadata(self)
        if not getattr(self, "_closeout_ready", False):
            return
        for row_index, row in enumerate(self._rows):
            if not isinstance(row, CloseoutJournalRow):
                continue
            status_item = self.table.item(row_index, 3)
            if status_item is None:
                continue
            if row.attendance == AttendanceStatus.UNKNOWN:
                status_item.setData(ATTENDANCE_TEXT_ROLE, "")
                status_item.setData(ATTENDANCE_TONE_ROLE, JournalTone.NEUTRAL.value)
                continue
            attendance_text = ATTENDANCE_LABELS[row.attendance]
            status_item.setData(ATTENDANCE_TEXT_ROLE, attendance_text)
            status_item.setData(
                ATTENDANCE_TONE_ROLE,
                _ATTENDANCE_TONES[row.attendance].value,
            )
            existing = str(
                status_item.data(Qt.ItemDataRole.AccessibleTextRole) or status_item.text()
            )
            accessible = f"{existing} Посещаемость: {attendance_text}."
            status_item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible)
            status_item.setToolTip(accessible)

    def _install_hardened_tab_order(self) -> None:
        smart_buttons = [
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
            *smart_buttons,
            self.unfinished_button,
            self.search,
            self.student_filter,
            self.subject_filter,
            self.period_filter,
            self.filters_toggle,
            self.attendance_filter,
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

    def keyboard_focus_zones(self) -> list[QWidget]:
        smart = self._smart_buttons.get(JournalSmartView.ALL)
        return [
            widget
            for widget in (smart, self.search, self.table, self.detail_attendance)
            if widget is not None
        ]

    def _guard_navigation(self) -> bool:
        return not self._note_dirty or self._resolve_dirty_before_context_change()

    def _open_selected_lesson(self) -> None:
        if self._guard_navigation():
            super()._open_selected_lesson()

    def _open_selected_materials(self) -> None:
        if self._guard_navigation():
            super()._open_selected_materials()

    def _open_selected_schedule(self) -> None:
        if self._guard_navigation():
            super()._open_selected_schedule()

    def closeEvent(self, event) -> None:
        if self._note_dirty and not self._resolve_dirty_before_context_change():
            event.ignore()
            return
        super().closeEvent(event)

    def _render(self, result, *, anchor=None) -> None:
        super()._render(result, anchor=anchor)
        self._sync_closeout_details()
