from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut

from .lesson_journal_closeout import LessonJournalCloseoutPage


class LessonJournalCloseoutStablePage(LessonJournalCloseoutPage):
    """Production closeout page with explicit lifecycle and keyboard stabilization."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.close_current_lesson)
        self.closeout_shortcuts.append(shortcut)

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

    def _render(self, result, *, anchor=None) -> None:
        super()._render(result, anchor=anchor)
        self._sync_closeout_details()
