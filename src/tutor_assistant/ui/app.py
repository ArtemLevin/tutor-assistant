from __future__ import annotations

import logging
import sys
import traceback
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDateEdit, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from ..config import AppConfig, load_students
from ..domain import JobStatus, Lesson
from ..pipeline import LessonPipeline
from ..recording import DualRecorder, list_input_devices


class Worker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, callable_, *args) -> None:
        super().__init__()
        self.callable = callable_
        self.args = args

    def run(self) -> None:
        try:
            self.succeeded.emit(self.callable(*self.args))
        except Exception:
            self.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config = AppConfig.load(config_path)
        self.pipeline = LessonPipeline(self.config)
        self.students = load_students(self.config.students_file)
        self.devices = list_input_devices()
        self.lesson: Lesson | None = None
        self.recorder: DualRecorder | None = None
        self.recording_seconds = 0
        self.workers: list[Worker] = []
        self.setWindowTitle("Tutor Assistant")
        self.resize(1000, 720)
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def _build(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._lesson_tab(), "1. Занятие")
        tabs.addTab(self._transcript_tab(), "2. Транскрипт")
        tabs.addTab(self._publish_tab(), "3. Публикация")
        self.setCentralWidget(tabs)
        self.statusBar().showMessage("Готово")

    def _lesson_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form_box = QGroupBox("Параметры занятия")
        form = QFormLayout(form_box)
        self.student = QComboBox()
        for item in self.students:
            self.student.addItem(item.full_name, item.id)
        self.subject = QComboBox()
        self.subject.addItems(["mathematics", "physics", "chemistry"])
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("Например: логарифмические неравенства")
        self.lesson_date = QDateEdit()
        self.lesson_date.setCalendarPopup(True)
        self.lesson_date.setDate(QDate.currentDate())
        self.mic = QComboBox()
        self.loopback = QComboBox()
        for device in self.devices:
            label = f"{device.index}: {device.name} [{device.host_api}]"
            self.mic.addItem(label, device.index)
            self.loopback.addItem(label, device.index)
        form.addRow("Ученик", self.student)
        form.addRow("Предмет", self.subject)
        form.addRow("Тема", self.topic)
        form.addRow("Дата", self.lesson_date)
        form.addRow("Микрофон", self.mic)
        form.addRow("Системный звук / loopback", self.loopback)
        layout.addWidget(form_box)

        row = QHBoxLayout()
        self.start_button = QPushButton("Начать запись")
        self.stop_button = QPushButton("Завершить запись")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_recording)
        self.stop_button.clicked.connect(self.stop_recording)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        self.duration = QLabel("00:00:00")
        row.addWidget(self.duration)
        row.addStretch()
        layout.addLayout(row)
        audio_row = QHBoxLayout()
        self.audio_path = QLineEdit()
        choose = QPushButton("Выбрать готовое аудио")
        choose.clicked.connect(self.choose_audio)
        audio_row.addWidget(self.audio_path)
        audio_row.addWidget(choose)
        layout.addLayout(audio_row)
        self.transcribe_button = QPushButton("Запустить локальную транскрибацию")
        self.transcribe_button.clicked.connect(self.transcribe)
        layout.addWidget(self.transcribe_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        layout.addWidget(self.progress)
        layout.addStretch()
        return page

    def _transcript_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "Проверьте числа, формулы и спорные фрагменты. После подтверждения текст попадёт в репозиторий ученика."
        ))
        self.transcript = QPlainTextEdit()
        layout.addWidget(self.transcript)
        self.approve = QPushButton("Подтвердить транскрипт")
        self.approve.setEnabled(False)
        self.approve.clicked.connect(self.approve_transcript)
        layout.addWidget(self.approve)
        return page

    def _publish_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.publish_summary = QLabel("Сначала создайте и подтвердите транскрипт.")
        self.publish_summary.setWordWrap(True)
        self.publish_button = QPushButton("Создать ветку и отправить задание")
        self.publish_button.setEnabled(False)
        self.publish_button.clicked.connect(self.publish)
        layout.addWidget(self.publish_summary)
        layout.addWidget(self.publish_button)
        layout.addStretch()
        return page

    def _make_lesson(self) -> Lesson:
        if not self.topic.text().strip():
            raise ValueError("Укажите тему занятия")
        selected = next(item for item in self.students if item.id == self.student.currentData())
        value = self.lesson_date.date()
        lesson = Lesson(
            student=selected,
            subject=self.subject.currentText(),
            topic=self.topic.text().strip(),
            lesson_date=date(value.year(), value.month(), value.day()),
        )
        self.pipeline.create(lesson)
        return lesson

    def start_recording(self) -> None:
        try:
            self.lesson = self._make_lesson()
            directory = self.pipeline.lesson_dir(self.lesson) / "recording"
            self.recorder = DualRecorder(self.config.recording.sample_rate, self.config.recording.channels)
            self.recorder.start(directory, int(self.mic.currentData()), int(self.loopback.currentData()))
            self.lesson.transition(JobStatus.RECORDING)
            self.pipeline.store.save(self.lesson)
            self.recording_seconds = 0
            self.timer.start(1000)
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.statusBar().showMessage("Идёт запись")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка записи", str(exc))

    def stop_recording(self) -> None:
        try:
            assert self.recorder and self.lesson
            result = self.recorder.stop()
            self.audio_path.setText(str(result.mixed_file))
            self.lesson.transition(JobStatus.RECORDED)
            self.pipeline.store.save(self.lesson)
            self.timer.stop()
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.statusBar().showMessage("Запись сохранена")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", str(exc))

    def choose_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Аудиозапись", "", "Audio (*.wav *.mp3 *.m4a *.flac)")
        if path:
            self.audio_path.setText(path)

    def transcribe(self) -> None:
        try:
            if self.lesson is None:
                self.lesson = self._make_lesson()
            audio = Path(self.audio_path.text())
            if not audio.is_file():
                raise ValueError("Выберите существующий аудиофайл")
            self.progress.setRange(0, 0)
            self.transcribe_button.setEnabled(False)
            worker = Worker(self.pipeline.transcribe, self.lesson, audio)
            worker.succeeded.connect(self._transcription_ready)
            worker.failed.connect(self._worker_failed)
            worker.finished.connect(lambda: self.workers.remove(worker))
            self.workers.append(worker)
            worker.start()
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", str(exc))

    def _transcription_ready(self, lesson: Lesson) -> None:
        self.lesson = lesson
        self.transcript.setPlainText(Path(lesson.artifacts.verified_transcript).read_text(encoding="utf-8"))
        self.approve.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.transcribe_button.setEnabled(True)
        self.statusBar().showMessage("Транскрипт ждёт проверки")

    def approve_transcript(self) -> None:
        assert self.lesson
        self.pipeline.approve_transcript(self.lesson, self.transcript.toPlainText())
        self.publish_summary.setText(
            f"{self.lesson.student.full_name}\n{self.lesson.lesson_date:%d.%m.%Y}\n"
            f"{self.lesson.topic}\n\nЗадание будет помещено в отдельную Git-ветку."
        )
        self.publish_button.setEnabled(True)
        self.statusBar().showMessage("Транскрипт подтверждён")

    def publish(self) -> None:
        assert self.lesson
        self.publish_button.setEnabled(False)
        worker = Worker(self.pipeline.publish, self.lesson)
        worker.succeeded.connect(lambda path: QMessageBox.information(
            self, "Готово", f"Задание опубликовано:\n{path}"
        ))
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(lambda: self.workers.remove(worker))
        self.workers.append(worker)
        worker.start()

    def _worker_failed(self, details: str) -> None:
        self.progress.setRange(0, 1)
        self.transcribe_button.setEnabled(True)
        self.publish_button.setEnabled(True)
        logging.error(details)
        QMessageBox.critical(self, "Ошибка фоновой операции", details[-3000:])

    def _tick(self) -> None:
        self.recording_seconds += 1
        hours, remainder = divmod(self.recording_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.duration.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    config = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/app.yaml")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow(config)
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
