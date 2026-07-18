from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..content import LessonImportRequest
from ..domain import Student
from .theme import set_button_kind


class ImportLessonDialog(QDialog):
    import_requested = Signal(object)
    cancellation_requested = Signal()
    progress_changed = Signal(str, int)

    def __init__(self, students: list[Student], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.students = students
        self._running = False
        self.setWindowTitle("Создать или импортировать занятие")
        self.setMinimumWidth(620)
        self._build()
        self.progress_changed.connect(self.set_progress)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        title = QLabel("Новое занятие в локальном архиве")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Можно создать пустую карточку или скопировать аудио и готовый транскрипт "
            "в управляемое хранилище Tutor Assistant."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.form_widget = QWidget()
        form = QFormLayout(self.form_widget)
        form.setVerticalSpacing(10)
        self.student = QComboBox()
        for item in sorted(self.students, key=lambda value: value.full_name.casefold()):
            self.student.addItem(item.full_name, item)
        form.addRow("Ученик", self.student)

        self.subject = QComboBox()
        self.subject.setEditable(True)
        subjects = {subject for item in self.students for subject in item.subjects}
        subjects.update({"mathematics", "physics", "chemistry"})
        self.subject.addItems(sorted(subjects, key=str.casefold))
        form.addRow("Предмет", self.subject)

        self.topic = QLineEdit()
        self.topic.setPlaceholderText("Тема занятия")
        form.addRow("Тема", self.topic)
        self.lesson_date = QDateEdit()
        self.lesson_date.setCalendarPopup(True)
        self.lesson_date.setDisplayFormat("dd.MM.yyyy")
        self.lesson_date.setDate(QDate.currentDate())
        form.addRow("Дата", self.lesson_date)

        self.audio_path = QLineEdit()
        self.audio_path.setReadOnly(True)
        self.audio_path.setPlaceholderText("Необязательно: WAV, MP3, M4A, AAC, FLAC или OGG")
        audio_row = QHBoxLayout()
        audio_row.addWidget(self.audio_path, 1)
        self.audio_button = set_button_kind(QPushButton("Выбрать…"), "ghost")
        self.audio_button.clicked.connect(self._choose_audio)
        audio_row.addWidget(self.audio_button)
        self.audio_clear_button = set_button_kind(QPushButton("×"), "ghost")
        self.audio_clear_button.setFixedWidth(34)
        self.audio_clear_button.setToolTip("Убрать аудиофайл")
        self.audio_clear_button.clicked.connect(self._clear_audio)
        audio_row.addWidget(self.audio_clear_button)
        form.addRow("Аудио", audio_row)

        self.transcript_path = QLineEdit()
        self.transcript_path.setReadOnly(True)
        self.transcript_path.setPlaceholderText("Необязательно: UTF-8 TXT или Markdown")
        transcript_row = QHBoxLayout()
        transcript_row.addWidget(self.transcript_path, 1)
        self.transcript_button = set_button_kind(QPushButton("Выбрать…"), "ghost")
        self.transcript_button.clicked.connect(self._choose_transcript)
        transcript_row.addWidget(self.transcript_button)
        self.transcript_clear_button = set_button_kind(QPushButton("×"), "ghost")
        self.transcript_clear_button.setFixedWidth(34)
        self.transcript_clear_button.setToolTip("Убрать транскрипт")
        self.transcript_clear_button.clicked.connect(self._clear_transcript)
        transcript_row.addWidget(self.transcript_clear_button)
        form.addRow("Транскрипт", transcript_row)

        self.enqueue_audio = QCheckBox("Поставить импортированное аудио в очередь транскрибации")
        self.enqueue_audio.setEnabled(False)
        form.addRow("После импорта", self.enqueue_audio)
        layout.addWidget(self.form_widget)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.state = QLabel("Файлы-источники не изменяются")
        self.state.setObjectName("muted")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = set_button_kind(QPushButton("Отмена"), "ghost")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.start_button = set_button_kind(QPushButton("Создать занятие"), "primary")
        self.start_button.clicked.connect(self._submit)
        actions.addWidget(self.start_button)
        layout.addLayout(actions)

    def _choose_audio(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Импорт аудио",
            "",
            "Аудио (*.wav *.mp3 *.m4a *.aac *.flac *.ogg)",
        )
        if path:
            self.audio_path.setText(path)
        self._sync_queue_option()

    def _choose_transcript(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Импорт транскрипта",
            "",
            "Текст (*.txt *.md *.markdown)",
        )
        if path:
            self.transcript_path.setText(path)
        self._sync_queue_option()

    def _sync_queue_option(self) -> None:
        allowed = bool(self.audio_path.text()) and not bool(self.transcript_path.text())
        self.enqueue_audio.setEnabled(allowed)
        if not allowed:
            self.enqueue_audio.setChecked(False)
        self.enqueue_audio.setToolTip(
            "Очередь недоступна при одновременном импорте готового транскрипта"
            if self.transcript_path.text()
            else "Транскрибация начнётся в штатной фоновой очереди"
        )

    def _clear_audio(self) -> None:
        self.audio_path.clear()
        self._sync_queue_option()

    def _clear_transcript(self) -> None:
        self.transcript_path.clear()
        self._sync_queue_option()

    def _submit(self) -> None:
        student = self.student.currentData()
        if not isinstance(student, Student):
            QMessageBox.warning(self, "Импорт", "Выберите ученика")
            return
        if not self.subject.currentText().strip():
            QMessageBox.warning(self, "Импорт", "Укажите предмет")
            return
        if not self.topic.text().strip():
            QMessageBox.warning(self, "Импорт", "Укажите тему занятия")
            return
        value = self.lesson_date.date()
        request = LessonImportRequest(
            student=student,
            subject=self.subject.currentText(),
            lesson_date=date(value.year(), value.month(), value.day()),
            topic=self.topic.text(),
            audio_source=Path(self.audio_path.text()) if self.audio_path.text() else None,
            transcript_source=Path(self.transcript_path.text()) if self.transcript_path.text() else None,
            enqueue_audio=self.enqueue_audio.isChecked(),
        )
        self.import_requested.emit(request)

    def set_running(self) -> None:
        self._running = True
        self.form_widget.setEnabled(False)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Отменить импорт")
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.state.setStyleSheet("")
        self.state.setText("Подготавливаю импорт…")

    def set_progress(self, message: str, percent: int) -> None:
        if not self._running:
            return
        self.state.setText(message)
        self.progress.setValue(percent)

    def show_error(self, details: str) -> None:
        self._running = False
        self.form_widget.setEnabled(True)
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Закрыть")
        self.state.setText(details)
        self.state.setStyleSheet("color: #A33636;")

    def finish_cancelled(self) -> None:
        self._running = False
        self.done(QDialog.Rejected)

    def finish_success(self) -> None:
        self._running = False
        self.progress.setValue(100)
        self.done(QDialog.Accepted)

    def reject(self) -> None:
        if self._running:
            self.cancellation_requested.emit()
            self.cancel_button.setEnabled(False)
            self.state.setText("Отменяю импорт и очищаю временные данные…")
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._running:
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)
