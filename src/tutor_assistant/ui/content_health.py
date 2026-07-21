from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..content import ContentIntegrityReport, IntegritySeverity
from ..content_browser import format_size
from .theme import set_button_kind

SEVERITY_LABELS = {
    IntegritySeverity.ERROR: "Ошибка",
    IntegritySeverity.WARNING: "Предупреждение",
    IntegritySeverity.INFO: "Информация",
}


class ContentHealthDialog(QDialog):
    rescan_requested = Signal()
    repair_requested = Signal()
    cleanup_requested = Signal()
    rebuild_search_requested = Signal()
    backup_requested = Signal()
    verify_backup_requested = Signal()
    restore_backup_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.report = ContentIntegrityReport()
        self.setWindowTitle("Диагностика хранилища")
        self.setAccessibleName("Диагностика локального архива материалов")
        self.resize(1040, 720)

        layout = QVBoxLayout(self)
        title = QLabel("Надёжность локального архива")
        title.setObjectName("tileTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Проверка SQLite, индекса поиска, управляемых файлов, потерянных каталогов "
            "и временных данных. Диагностика ничего не удаляет автоматически."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.summary = QLabel("Проверка ещё не запускалась")
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName("Сводка диагностики")
        layout.addWidget(self.summary)
        self.storage = QLabel("")
        self.storage.setObjectName("muted")
        self.storage.setWordWrap(True)
        self.storage.setAccessibleName("Использование дискового пространства")
        layout.addWidget(self.storage)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Уровень", "Код", "Занятие", "Путь", "Описание"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setAccessibleName("Обнаруженные проблемы хранилища")
        layout.addWidget(self.table, 1)

        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.state.setAccessibleName("Состояние операции диагностики")
        layout.addWidget(self.state)

        actions = QHBoxLayout()
        self.rescan_button = set_button_kind(QPushButton("Проверить снова"), "primary")
        self.rescan_button.setToolTip("Повторить проверку · F5")
        self.rescan_button.clicked.connect(self.rescan_requested.emit)
        actions.addWidget(self.rescan_button)
        self.repair_button = set_button_kind(QPushButton("Безопасно восстановить"), "ghost")
        self.repair_button.setToolTip("Восстановить проекции из SQLite и зарегистрировать найденные файлы")
        self.repair_button.clicked.connect(self.repair_requested.emit)
        actions.addWidget(self.repair_button)
        self.rebuild_button = set_button_kind(QPushButton("Перестроить поиск"), "ghost")
        self.rebuild_button.setToolTip("Пересоздать полнотекстовый индекс · Ctrl+R")
        self.rebuild_button.clicked.connect(self.rebuild_search_requested.emit)
        actions.addWidget(self.rebuild_button)
        self.cleanup_button = set_button_kind(QPushButton("Очистить временные"), "danger")
        self.cleanup_button.setToolTip("Удалить только старые известные staging/tmp · Ctrl+Delete")
        self.cleanup_button.clicked.connect(self._confirm_cleanup)
        actions.addWidget(self.cleanup_button)
        self.backup_button = set_button_kind(QPushButton("Создать backup"), "ghost")
        self.backup_button.setToolTip("Создать согласованную резервную копию SQLite")
        self.backup_button.clicked.connect(self.backup_requested.emit)
        self.verify_backup_button = set_button_kind(QPushButton("Проверить backup…"), "ghost")
        self.verify_backup_button.clicked.connect(self.verify_backup_requested.emit)
        self.restore_backup_button = set_button_kind(QPushButton("Восстановить…"), "danger")
        self.restore_backup_button.setToolTip("Проверить и восстановить SQLite из резервной копии")
        self.restore_backup_button.clicked.connect(self.restore_backup_requested.emit)
        actions.addStretch(1)
        close = set_button_kind(QPushButton("Закрыть"), "ghost")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)

        backup_actions = QHBoxLayout()
        backup_label = QLabel("Резервные копии SQLite")
        backup_label.setObjectName("muted")
        backup_actions.addWidget(backup_label)
        backup_actions.addStretch(1)
        backup_actions.addWidget(self.backup_button)
        backup_actions.addWidget(self.verify_backup_button)
        backup_actions.addWidget(self.restore_backup_button)
        layout.addLayout(backup_actions)

        self.rescan_shortcut = QShortcut(QKeySequence.Refresh, self)
        self.rescan_shortcut.activated.connect(self.rescan_requested.emit)
        self.rebuild_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        self.rebuild_shortcut.activated.connect(self.rebuild_search_requested.emit)
        self.cleanup_shortcut = QShortcut(QKeySequence("Ctrl+Delete"), self)
        self.cleanup_shortcut.activated.connect(self._confirm_cleanup)

    def set_report(self, report: ContentIntegrityReport) -> None:
        self.report = report
        tone = "#277A44" if report.healthy and not report.warnings else "#A15C00"
        if not report.healthy:
            tone = "#A33636"
        result = "Проверка пройдена"
        if report.healthy and report.warnings:
            result = "Проверка пройдена с предупреждениями"
        elif not report.healthy:
            result = "Требуется внимание"
        self.summary.setStyleSheet(f"color: {tone};")
        self.summary.setText(
            f"{result} · "
            f"ошибок: {report.errors} · предупреждений: {report.warnings} · "
            f"SQLite: {report.database_message} · "
            f"поиск: {'FTS5' if report.fts_enabled else 'fallback'} "
            f"({report.fts_documents} документов) · "
            f"режим: {report.scan.mode.value} · "
            f"проверено занятий: {report.scan.lessons_examined} · "
            f"SHA-256: {report.scan.assets_hashed} · "
            f"cache hits: {report.scan.asset_cache_hits} · "
            f"{report.scan.duration_ms} мс"
        )
        usage = report.storage
        self.storage.setText(
            f"Занятия: {format_size(usage.lessons_bytes)} · "
            f"корзина: {format_size(usage.trash_bytes)} · "
            f"временные: {format_size(usage.temporary_bytes)} · "
            f"SQLite: {format_size(usage.database_bytes)} · "
            f"всего управляется: {format_size(usage.managed_bytes)} · "
            f"свободно: {format_size(usage.free_bytes)}"
        )
        self.table.setRowCount(len(report.issues))
        colors = {
            IntegritySeverity.ERROR: QColor("#A33636"),
            IntegritySeverity.WARNING: QColor("#A15C00"),
            IntegritySeverity.INFO: QColor("#315D8A"),
        }
        for row, issue in enumerate(report.issues):
            values = (
                SEVERITY_LABELS[issue.severity],
                issue.code,
                issue.lesson_id or "—",
                issue.relative_path or "—",
                issue.message,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setForeground(QBrush(colors[issue.severity]))
                self.table.setItem(row, column, item)
        self.state.setStyleSheet("")
        self.state.setText("")
        self._set_buttons_enabled(True)
        self.cleanup_button.setEnabled(bool(report.temporary_paths))
        self.cleanup_button.setText(f"Очистить временные ({len(report.temporary_paths)})")
        self.rebuild_button.setEnabled(report.fts_enabled)
        self.repair_button.setEnabled(bool(report.issues))

    def set_busy(self, message: str) -> None:
        self._set_buttons_enabled(False)
        self.state.setStyleSheet("")
        self.state.setText(message)

    def show_error(self, message: str) -> None:
        self._set_buttons_enabled(True)
        self.cleanup_button.setEnabled(bool(self.report.temporary_paths))
        self.rebuild_button.setEnabled(self.report.fts_enabled)
        self.repair_button.setEnabled(bool(self.report.issues))
        self.state.setStyleSheet("color: #A33636;")
        self.state.setText(message)

    def show_result(self, message: str) -> None:
        self._set_buttons_enabled(True)
        self.cleanup_button.setEnabled(bool(self.report.temporary_paths))
        self.rebuild_button.setEnabled(self.report.fts_enabled)
        self.repair_button.setEnabled(bool(self.report.issues))
        self.state.setStyleSheet("color: #277A44;")
        self.state.setText(message)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self.rescan_button.setEnabled(enabled)
        self.repair_button.setEnabled(enabled)
        self.rebuild_button.setEnabled(enabled)
        self.cleanup_button.setEnabled(enabled)
        self.backup_button.setEnabled(enabled)
        self.verify_backup_button.setEnabled(enabled)
        self.restore_backup_button.setEnabled(enabled)

    def _confirm_cleanup(self) -> None:
        if not self.report.temporary_paths:
            return
        answer = QMessageBox.warning(
            self,
            "Очистить временные данные",
            f"Удалить старые известные staging/tmp-объекты: {len(self.report.temporary_paths)}? "
            "Потерянные каталоги занятий затронуты не будут.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer == QMessageBox.Yes:
            self.set_busy("Удаляю старые временные данные…")
            self.cleanup_requested.emit()
