from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QTableWidgetItem

from ..crm import ScheduledLesson
from .lesson_journal_ux import LessonJournalUXPage


class LessonJournalUXStablePage(LessonJournalUXPage):
    """Stabilized UX layer used as the production baseline for the journal."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        debt_index = self.payment_filter.findData("unpaid_past")
        if debt_index >= 0:
            self.payment_filter.setItemText(debt_index, "Есть задолженность")
        self._update_filter_ui()

    def _cancel_pending_refresh(self) -> None:
        if hasattr(self, "_debounce"):
            self._debounce.stop()

    def refresh(self, *, preserve_context: bool = False, anchor=None) -> None:
        if preserve_context:
            self._cancel_pending_refresh()
        super().refresh(preserve_context=preserve_context, anchor=anchor)

    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        self._cancel_pending_refresh()
        super()._table_item_changed(item)

    def _homework_changed(self, lesson: ScheduledLesson, combo: QComboBox) -> None:
        self._cancel_pending_refresh()
        super()._homework_changed(lesson, combo)

    def _detail_payment_changed(self, paid: bool) -> None:
        self._cancel_pending_refresh()
        super()._detail_payment_changed(paid)

    def _detail_homework_changed(self, index: int) -> None:
        self._cancel_pending_refresh()
        super()._detail_homework_changed(index)

    def _save_due(self) -> None:
        self._cancel_pending_refresh()
        super()._save_due()

    def _show_more(self) -> None:
        self._cancel_pending_refresh()
        super()._show_more()
