from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)


class JournalKeyboardController(QObject):
    """Keyboard workflow controller for the lesson journal workspace."""

    def __init__(self, page) -> None:
        super().__init__(page)
        self.page = page
        self.shortcuts: list[QShortcut] = []
        self._add_shortcut(QKeySequence.StandardKey.Find, page.focus_search)
        self._add_shortcut("Ctrl+Shift+F", page.toggle_advanced_filters)
        self._add_shortcut(QKeySequence.StandardKey.Undo, self.undo)
        self._add_shortcut("Esc", page.handle_escape)
        self._add_shortcut("F6", lambda: self.cycle_focus(1))
        self._add_shortcut("Shift+F6", lambda: self.cycle_focus(-1))
        page.table.installEventFilter(self)

    def _add_shortcut(self, sequence, callback: Callable[[], None]) -> None:
        shortcut = QShortcut(QKeySequence(sequence), self.page)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(callback)
        self.shortcuts.append(shortcut)

    def undo(self) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit, QTextEdit)):
            focus.undo()
            return
        self.page.undo_last_action()

    @staticmethod
    def _contains(zone: QWidget, focus: QWidget | None) -> bool:
        if focus is None:
            return False
        return focus is zone or zone.isAncestorOf(focus)

    def cycle_focus(self, direction: int) -> None:
        zones = [zone for zone in self.page.keyboard_focus_zones() if zone.isVisible()]
        if not zones:
            return
        focus = QApplication.focusWidget()
        current = next(
            (index for index, zone in enumerate(zones) if self._contains(zone, focus)),
            -1,
        )
        target = zones[(current + direction) % len(zones)]
        target.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.page.table and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                self.page.activate_current_row()
                return True
            if key == Qt.Key.Key_Space:
                self.page.toggle_current_payment()
                return True
            if key == Qt.Key.Key_F2:
                self.page.focus_current_homework()
                return True
        return super().eventFilter(watched, event)
