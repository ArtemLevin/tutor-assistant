from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..content import ContentOperation, TrashState, TrashSummary
from ..content_browser import format_size
from .theme import set_button_kind

OPERATION_LABELS = {
    "delete": "В корзину",
    "restore": "Восстановление",
    "purge": "Удаление навсегда",
}

STATUS_LABELS = {
    "pending": "Выполняется",
    "cleanup_pending": "Ожидает очистки",
    "completed": "Завершено",
    "failed": "Ошибка",
}


class ContentTrashDialog(QDialog):
    restore_requested = Signal(str)
    purge_requested = Signal(str)
    purge_expired_requested = Signal()
    retention_changed = Signal(int)
    refresh_requested = Signal()

    def __init__(self, retention_days: int, parent=None) -> None:
        super().__init__(parent)
        self.summary = TrashSummary()
        self.setWindowTitle("Корзина материалов")
        self.setAccessibleName("Корзина локальных материалов")
        self.resize(980, 720)

        layout = QVBoxLayout(self)
        title = QLabel("Корзина занятий")
        title.setObjectName("tileTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Удалённые занятия хранятся локально. Уже опубликованные материалы GitHub не изменяются."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        retention = QHBoxLayout()
        retention.addWidget(QLabel("Хранить удалённые занятия"))
        self.retention_days = QSpinBox()
        self.retention_days.setRange(0, 3650)
        self.retention_days.setSuffix(" дн.")
        self.retention_days.setValue(retention_days)
        self.retention_days.setAccessibleName("Срок хранения удалённых занятий в днях")
        retention.addWidget(self.retention_days)
        save_retention = set_button_kind(QPushButton("Сохранить срок"), "ghost")
        save_retention.clicked.connect(lambda: self.retention_changed.emit(self.retention_days.value()))
        retention.addWidget(save_retention)
        retention.addStretch(1)
        self.summary_label = QLabel("Корзина загружается…")
        self.summary_label.setObjectName("muted")
        retention.addWidget(self.summary_label)
        layout.addLayout(retention)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Удалено", "Ученик", "Тема", "Размер", "Автоочистка"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAccessibleName("Удалённые занятия")
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 2)

        actions = QHBoxLayout()
        self.state = QLabel("")
        self.state.setWordWrap(True)
        actions.addWidget(self.state, 1)
        self.restore_button = set_button_kind(QPushButton("Восстановить"), "ghost")
        self.restore_button.setEnabled(False)
        self.restore_button.clicked.connect(self._restore)
        actions.addWidget(self.restore_button)
        self.purge_button = set_button_kind(QPushButton("Удалить навсегда"), "danger")
        self.purge_button.setEnabled(False)
        self.purge_button.clicked.connect(self._purge)
        actions.addWidget(self.purge_button)
        self.purge_expired_button = set_button_kind(QPushButton("Очистить просроченные"), "danger")
        self.purge_expired_button.setEnabled(False)
        self.purge_expired_button.clicked.connect(self._purge_expired)
        actions.addWidget(self.purge_expired_button)
        layout.addLayout(actions)

        log_title = QLabel("Журнал операций")
        log_title.setObjectName("eyebrow")
        layout.addWidget(log_title)
        self.operations = QTableWidget(0, 5)
        self.operations.setHorizontalHeaderLabels(["Время", "Операция", "Занятие", "Статус", "Объём"])
        self.operations.setEditTriggers(QTableWidget.NoEditTriggers)
        self.operations.setSelectionMode(QTableWidget.NoSelection)
        self.operations.setAccessibleName("Журнал операций корзины")
        self.operations.verticalHeader().setVisible(False)
        self.operations.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.operations, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close = set_button_kind(QPushButton("Закрыть"), "primary")
        close.clicked.connect(self.accept)
        close_row.addWidget(close)
        layout.addLayout(close_row)

        self.refresh_shortcut = QShortcut(QKeySequence.Refresh, self)
        self.refresh_shortcut.activated.connect(self.refresh_requested.emit)
        self.restore_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        self.restore_shortcut.activated.connect(self._restore)
        self.purge_shortcut = QShortcut(QKeySequence("Ctrl+Delete"), self)
        self.purge_shortcut.activated.connect(self._purge)
        self.restore_button.setToolTip("Восстановить выбранное занятие · Ctrl+R")
        self.purge_button.setToolTip("Удалить выбранное занятие навсегда · Ctrl+Delete")

    def set_data(self, summary: TrashSummary, operations: list[ContentOperation]) -> None:
        self.summary = summary
        self.table.setRowCount(len(summary.items))
        for row, item in enumerate(summary.items):
            entry = item.entry
            state = (
                entry.purge_after.astimezone().strftime("%d.%m.%Y")
                if entry.state == TrashState.TRASHED
                else "Операция восстанавливается"
            )
            values = (
                entry.deleted_at.astimezone().strftime("%d.%m.%Y %H:%M"),
                item.lesson.student.full_name,
                item.lesson.topic,
                format_size(entry.size_bytes),
                state,
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.UserRole, item.lesson.lesson_id)
                cell.setData(Qt.UserRole + 1, entry.state.value)
                self.table.setItem(row, column, cell)
        self.summary_label.setText(f"{len(summary.items)} занятий · {format_size(summary.total_size_bytes)}")
        self.purge_expired_button.setEnabled(summary.expired_count > 0)
        self.purge_expired_button.setText(f"Очистить просроченные ({summary.expired_count})")

        self.operations.setRowCount(len(operations))
        for row, operation in enumerate(operations):
            values = (
                operation.created_at.astimezone().strftime("%d.%m.%Y %H:%M"),
                OPERATION_LABELS.get(operation.operation.value, operation.operation.value),
                operation.lesson_id,
                STATUS_LABELS.get(operation.status.value, operation.status.value),
                format_size(operation.size_bytes),
            )
            for column, value in enumerate(values):
                self.operations.setItem(row, column, QTableWidgetItem(value))
        self.state.setStyleSheet("")
        self.state.setText("")
        self._selection_changed()

    def selected_lesson_id(self) -> str | None:
        items = self.table.selectedItems()
        return str(items[0].data(Qt.UserRole)) if items else None

    def _selection_changed(self) -> None:
        items = self.table.selectedItems()
        trashed = bool(items) and items[0].data(Qt.UserRole + 1) == TrashState.TRASHED.value
        self.restore_button.setEnabled(trashed)
        self.purge_button.setEnabled(trashed)

    def _restore(self) -> None:
        lesson_id = self.selected_lesson_id()
        if lesson_id:
            self.set_busy("Восстанавливаю занятие…")
            self.restore_requested.emit(lesson_id)

    def _purge(self) -> None:
        lesson_id = self.selected_lesson_id()
        if not lesson_id:
            return
        answer = QMessageBox.warning(
            self,
            "Удалить занятие навсегда",
            "Локальные файлы, транскрипты и история версий будут удалены без возможности "
            "восстановления. Опубликованный GitHub-контент останется без изменений.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer == QMessageBox.Yes:
            self.set_busy("Удаляю локальные данные навсегда…")
            self.purge_requested.emit(lesson_id)

    def _purge_expired(self) -> None:
        if self.summary.expired_count == 0:
            return
        answer = QMessageBox.warning(
            self,
            "Очистить просроченные занятия",
            f"Безвозвратно удалить локальные данные: {self.summary.expired_count}?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer == QMessageBox.Yes:
            self.set_busy("Очищаю просроченные занятия…")
            self.purge_expired_requested.emit()

    def set_busy(self, message: str) -> None:
        self.table.setEnabled(False)
        self.restore_button.setEnabled(False)
        self.purge_button.setEnabled(False)
        self.purge_expired_button.setEnabled(False)
        self.state.setStyleSheet("")
        self.state.setText(message)

    def show_error(self, message: str) -> None:
        self.table.setEnabled(True)
        self.state.setStyleSheet("color: #A33636;")
        self.state.setText(message)
        self._selection_changed()
        self.purge_expired_button.setEnabled(self.summary.expired_count > 0)
