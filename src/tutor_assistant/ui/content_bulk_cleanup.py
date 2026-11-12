"""Explicit, asynchronous UI for destructive local archive cleanup."""

from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ..content import BulkCleanupPreview, BulkCleanupProgress, BulkCleanupResult, StudentContentService
from ..content_browser import format_size
from .theme import set_button_kind


class BulkCleanupWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progressed = Signal(object)

    def __init__(self, service: StudentContentService, fingerprint: str, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.fingerprint = fingerprint

    def run(self) -> None:
        try:
            result = self.service.run_bulk_cleanup(
                self.fingerprint,
                progress=self.progressed.emit,
            )
            self.succeeded.emit(result)
        except Exception:
            logging.exception("Bulk cleanup failed before completion")
            self.failed.emit(traceback.format_exc())


class BulkCleanupDialog(QDialog):
    confirmed = Signal(str)

    def __init__(self, preview: BulkCleanupPreview, parent=None) -> None:
        super().__init__(parent)
        self.preview = preview
        self.running = False
        self.setWindowTitle("Очистка всех локальных материалов")
        self.setAccessibleName("Очистка всех локальных материалов")
        self.resize(670, 390)
        layout = QVBoxLayout(self)

        title = QLabel("Очистить весь управляемый локальный архив?")
        title.setObjectName("tileTitle")
        layout.addWidget(title)
        description = QLabel(
            "Будут безвозвратно удалены аудиозаписи, транскрипты, документы и история "
            "всех занятий, включая корзину. Карточки учеников, расписание и платежи "
            "останутся. Опубликованные материалы GitHub не изменятся. "
            "Резервные копии, журналы и неизвестные файлы сохраняются."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self.summary = QLabel(
            f"Рабочий каталог: {preview.workspace}\n"
            f"Занятия: {preview.active_lessons_count}; "
            f"корзина: {preview.trashed_lessons_count}\n"
            f"Оценка объёма: {format_size(preview.estimated_total_bytes)} "
            f"(резервные копии сохраняются: {format_size(preview.backups_bytes)})"
        )
        self.summary.setAccessibleName("Обзор очищаемых материалов")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.state = QLabel(
            "\n".join(preview.blockers)
            if preview.blockers
            else "Подтверждение необратимо. Внешние файлы не затрагиваются."
        )
        self.state.setWordWrap(True)
        self.state.setAccessibleName("Состояние очистки")
        layout.addWidget(self.state)

        self.progress = QProgressBar()
        self.progress.setRange(0, max(1, preview.total_lessons_count))
        self.progress.setVisible(False)
        self.progress.setAccessibleName("Прогресс удаления материалов")
        layout.addWidget(self.progress)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.close_button = set_button_kind(QPushButton("Отмена"), "ghost")
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        self.confirm_button = set_button_kind(QPushButton("Удалить безвозвратно"), "danger")
        self.confirm_button.setEnabled(not preview.blockers and preview.total_lessons_count > 0)
        self.confirm_button.clicked.connect(self._confirm)
        actions.addWidget(self.confirm_button)
        layout.addLayout(actions)

    def _confirm(self) -> None:
        if self.preview.blockers or self.running:
            return
        answer = QMessageBox.warning(
            self,
            "Подтвердите безвозвратное удаление",
            "Удалить все локальные материалы занятий без возможности восстановления "
            "из активного архива? Резервные копии сохранятся.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer == QMessageBox.Yes:
            self.confirmed.emit(self.preview.fingerprint)

    def set_running(self) -> None:
        self.running = True
        self.confirm_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.progress.setVisible(True)
        self.state.setText("Удаление выполняется. Не закрывайте приложение.")

    def update_progress(self, item: BulkCleanupProgress) -> None:
        self.progress.setMaximum(max(1, item.total))
        self.progress.setValue(item.processed)
        self.state.setText(f"Обработано {item.processed} из {item.total}; удалено {item.purged}")

    def show_result(self, result: BulkCleanupResult) -> None:
        self.running = False
        self.close_button.setEnabled(True)
        self.close_button.setText("Закрыть")
        self.confirm_button.setEnabled(False)
        self.state.setText(
            f"Статус: {result.state}. Удалено: {result.purged} из {result.total}. "
            f"Осталось: {result.remaining_count}. "
            f"Логический размер файлов: {format_size(result.removed_bytes)}. "
            f"Изменение свободного места: "
            f"{result.free_after - result.free_before:+d} байт. "
            f"Ошибок: {len(result.failures)}; предупреждений: {len(result.warnings)}."
        )
        if result.failures:
            self.state.setToolTip("\n".join(
                f"{item.lesson_id}: {item.details}" for item in result.failures
            )[:4000])

    def show_error(self, details: str) -> None:
        self.running = False
        self.confirm_button.setEnabled(False)
        self.close_button.setEnabled(True)
        self.close_button.setText("Закрыть")
        self.state.setText("Очистка остановлена: " + details[-1500:])

    def reject(self) -> None:
        if not self.running:
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.running:
            event.ignore()
            return
        super().closeEvent(event)
