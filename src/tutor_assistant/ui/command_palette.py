from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from PySide6.QtCore import Qt
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
        root.addWidget(self.search)

        self.results = QListWidget()
        self.results.setObjectName("commandPaletteResults")
        self.results.setAccessibleName("Результаты командной палитры")
        self.results.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.results.itemActivated.connect(lambda _item: self.execute_current())
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
        if self.results.count():
            self.results.setCurrentRow(0)

    def execute_current(self) -> None:
        row = self.results.currentRow()
        if not 0 <= row < len(self._visible_commands):
            return
        command = self._visible_commands[row]
        if not command.enabled:
            return
        self.accept()
        command.callback()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.execute_current()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Down:
            self.results.setFocus(Qt.FocusReason.TabFocusReason)
            current = max(0, self.results.currentRow())
            self.results.setCurrentRow(min(self.results.count() - 1, current + 1))
            event.accept()
            return
        if event.key() == Qt.Key.Key_Up and self.results.hasFocus():
            current = max(0, self.results.currentRow())
            if current == 0:
                self.search.setFocus(Qt.FocusReason.TabFocusReason)
            else:
                self.results.setCurrentRow(current - 1)
            event.accept()
            return
        super().keyPressEvent(event)
