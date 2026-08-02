from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class PaletteCommand:
    command_id: str
    title: str
    subtitle: str
    callback: Callable[[], None]
    keywords: tuple[str, ...] = ()
    shortcut: str = ""
    enabled: bool = True

    @property
    def search_text(self) -> str:
        return " ".join((self.title, self.subtitle, *self.keywords)).casefold()


class CommandPalette(QDialog):
    """Keyboard-first command launcher used across the production window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Командная палитра")
        self.setModal(True)
        self.setMinimumSize(680, 440)
        self.resize(760, 520)
        self.setObjectName("commandPalette")
        self.setAccessibleName("Командная палитра Tutor Assistant")
        self._commands: list[PaletteCommand] = []
        self._visible_commands: list[PaletteCommand] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Перейти или выполнить действие")
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)
        hint = QLabel("Ctrl+K")
        hint.setObjectName("statusPill")
        header.addWidget(hint)
        root.addLayout(header)

        self.search = QLineEdit()
        self.search.setObjectName("commandPaletteSearch")
        self.search.setPlaceholderText("Маршрут, ученик, занятие или команда")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Поиск по командам")
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self.execute_current)
        self.search.installEventFilter(self)
        root.addWidget(self.search)

        self.results = QListWidget()
        self.results.setObjectName("commandPaletteResults")
        self.results.setAccessibleName("Результаты командной палитры")
        self.results.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.results.itemActivated.connect(lambda _item: self.execute_current())
        self.results.installEventFilter(self)
        root.addWidget(self.results, 1)

        footer = QLabel("↑ ↓ — выбор · Enter — выполнить · Esc — закрыть")
        footer.setObjectName("muted")
        root.addWidget(footer)

    def set_commands(self, commands: Iterable[PaletteCommand]) -> None:
        self._commands = list(commands)
        self._filter(self.search.text())

    def open_with_commands(self, commands: Iterable[PaletteCommand]) -> None:
        self.set_commands(commands)
        self.search.clear()
        self.open()
        self.raise_()
        self.activateWindow()
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)

    @staticmethod
    def _score(command: PaletteCommand, query: str) -> tuple[int, int, str] | None:
        tokens = [token for token in query.casefold().split() if token]
        if not tokens:
            return 0, 0, command.title.casefold()
        haystack = command.search_text
        if any(token not in haystack for token in tokens):
            return None
        title = command.title.casefold()
        exact = sum(token == title for token in tokens)
        prefix = sum(title.startswith(token) for token in tokens)
        return -exact, -prefix, title

    def _filter(self, query: str) -> None:
        ranked: list[tuple[tuple[int, int, str], PaletteCommand]] = []
        for command in self._commands:
            score = self._score(command, query)
            if score is not None:
                ranked.append((score, command))
        ranked.sort(key=lambda pair: pair[0])
        self._visible_commands = [command for _score, command in ranked]

        self.results.clear()
        for index, command in enumerate(self._visible_commands):
            suffix = f"    {command.shortcut}" if command.shortcut else ""
            item = QListWidgetItem(f"{command.title}{suffix}\n{command.subtitle}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            if not command.enabled:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            item.setToolTip(command.subtitle)
            self.results.addItem(item)
        self._select_first_enabled()

    def _enabled_rows(self) -> list[int]:
        return [
            row
            for row, command in enumerate(self._visible_commands)
            if command.enabled
        ]

    def _select_first_enabled(self) -> None:
        enabled_rows = self._enabled_rows()
        self.results.setCurrentRow(enabled_rows[0] if enabled_rows else -1)

    def _move_selection(self, delta: int) -> None:
        enabled_rows = self._enabled_rows()
        if not enabled_rows:
            return
        current = self.results.currentRow()
        if current not in enabled_rows:
            target = enabled_rows[0 if delta >= 0 else -1]
        else:
            position = enabled_rows.index(current)
            target = enabled_rows[(position + delta) % len(enabled_rows)]
        self.results.setCurrentRow(target)
        self.results.setFocus(Qt.FocusReason.TabFocusReason)

    def execute_current(self) -> None:
        row = self.results.currentRow()
        if not 0 <= row < len(self._visible_commands):
            return
        command = self._visible_commands[row]
        if not command.enabled:
            return
        self.accept()
        command.callback()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            return True
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.execute_current()
            return True
        if key == Qt.Key.Key_Down:
            self._move_selection(1)
            return True
        if key == Qt.Key.Key_Up:
            enabled_rows = self._enabled_rows()
            if (
                watched is self.results
                and enabled_rows
                and self.results.currentRow() == enabled_rows[0]
            ):
                self.search.setFocus(Qt.FocusReason.TabFocusReason)
            else:
                self._move_selection(-1)
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)
