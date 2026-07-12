from __future__ import annotations

import logging
import json
import sys
import traceback
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QThread, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDateEdit, QFileDialog, QFormLayout, QGroupBox,
    QHeaderView, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..config import AppConfig, load_students
from ..domain import JobStatus, Lesson
from ..pipeline import LessonPipeline
from ..recording import (
    DualRecorder, find_recoverable_recordings, list_input_devices, recover_recording, test_input_device,
)


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
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.play_stop_timer = QTimer(self)
        self.play_stop_timer.setSingleShot(True)
        self.play_stop_timer.timeout.connect(self.player.pause)
        self.setWindowTitle("Tutor Assistant")
        self.resize(1000, 720)
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        QTimer.singleShot(0, self._offer_recovery)

    def _build(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._lesson_tab(), "1. Занятие")
        tabs.addTab(self._transcript_tab(), "2. Транскрипт")
        tabs.addTab(self._publish_tab(), "3. Публикация")
        self.setCentralWidget(tabs)
        self.statusBar().showMessage("Готово")

    def _offer_recovery(self) -> None:
        sessions = find_recoverable_recordings(self.config.workspace)
        if not sessions:
            return
        directory = sessions[-1]
        answer = QMessageBox.question(
            self,
            "Незавершённая запись",
            f"Найдены сохранённые чанки:\n{directory}\n\nВосстановить аудиозапись?",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            result = recover_recording(directory)
            self.audio_path.setText(str(result.mixed_file))
            session = json.loads(result.session_file.read_text(encoding="utf-8"))
            session["status"] = "recovered"
            result.session_file.write_text(
                json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            QMessageBox.information(self, "Восстановление", f"Запись восстановлена:\n{result.mixed_file}")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка восстановления", str(exc))

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

        diagnostics = QGroupBox("Проверка устройств")
        diagnostics_layout = QFormLayout(diagnostics)
        self.mic_level = QProgressBar()
        self.mic_level.setRange(0, 100)
        self.system_level = QProgressBar()
        self.system_level.setRange(0, 100)
        diagnostics_layout.addRow("Микрофон", self.mic_level)
        diagnostics_layout.addRow("Системный звук", self.system_level)
        self.test_devices_button = QPushButton("Записать тестовый сигнал")
        self.test_devices_button.clicked.connect(self.test_devices)
        diagnostics_layout.addRow(self.test_devices_button)
        layout.addWidget(diagnostics)

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
        self.segment_table = QTableWidget(0, 4)
        self.segment_table.setHorizontalHeaderLabels(["Начало", "Конец", "Текст", "Уверенность"])
        self.segment_table.horizontalHeader().setStretchLastSection(False)
        self.segment_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.segment_table.setAlternatingRowColors(True)
        self.segment_table.doubleClicked.connect(self.play_selected_segment)
        layout.addWidget(self.segment_table, 4)
        controls = QHBoxLayout()
        self.play_segment_button = QPushButton("▶ Воспроизвести выбранный сегмент")
        self.play_segment_button.clicked.connect(self.play_selected_segment)
        self.playback_speed = QComboBox()
        for label, value in [("0,75×", 0.75), ("1×", 1.0), ("1,25×", 1.25)]:
            self.playback_speed.addItem(label, value)
        controls.addWidget(self.play_segment_button)
        controls.addWidget(QLabel("Скорость"))
        controls.addWidget(self.playback_speed)
        controls.addStretch()
        layout.addLayout(controls)
        layout.addWidget(QLabel("Сводный текст — используется, если таблица сегментов пуста:"))
        self.transcript = QPlainTextEdit()
        layout.addWidget(self.transcript, 1)
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
            self.recorder = DualRecorder(
                self.config.recording.sample_rate,
                self.config.recording.channels,
                self.config.recording.chunk_seconds,
            )
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

    def test_devices(self) -> None:
        self.test_devices_button.setEnabled(False)
        self.statusBar().showMessage("Проверяю микрофон и системный звук…")
        mic_device = int(self.mic.currentData())
        loopback_device = int(self.loopback.currentData())
        seconds = self.config.recording.diagnostics_seconds
        sample_rate = self.config.recording.sample_rate
        channels = self.config.recording.channels

        def run_tests():
            mic = test_input_device(mic_device, seconds, sample_rate, channels)
            system = test_input_device(loopback_device, seconds, sample_rate, channels)
            return mic, system

        worker = Worker(run_tests)
        worker.succeeded.connect(self._device_test_ready)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(lambda: self.workers.remove(worker))
        self.workers.append(worker)
        worker.start()

    def _device_test_ready(self, results) -> None:
        mic, system = results
        self.mic_level.setValue(round(min(1.0, mic.rms * 5) * 100))
        self.system_level.setValue(round(min(1.0, system.rms * 5) * 100))
        warnings = []
        if mic.silent:
            warnings.append("микрофон почти не передаёт сигнал")
        if system.silent:
            warnings.append("системный вход почти не передаёт сигнал")
        if mic.clipped or system.clipped:
            warnings.append("обнаружен перегруз")
        message = "Проверка пройдена." if not warnings else "Проверьте настройки: " + "; ".join(warnings)
        QMessageBox.information(self, "Диагностика аудио", message)
        self.test_devices_button.setEnabled(True)
        self.statusBar().showMessage(message)

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
        self._load_segments(Path(lesson.artifacts.segments_json))
        self.approve.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.transcribe_button.setEnabled(True)
        self.statusBar().showMessage("Транскрипт ждёт проверки")

    def _load_segments(self, path: Path) -> None:
        segments = json.loads(path.read_text(encoding="utf-8"))
        self.segment_table.setRowCount(len(segments))
        for row, segment in enumerate(segments):
            start = float(segment["start"])
            end = float(segment["end"])
            confidence = segment.get("avg_logprob")
            confidence_text = "—" if confidence is None else f"{min(100, max(0, round((1 + float(confidence)) * 100)))}%"
            start_item = QTableWidgetItem(self._format_time(start))
            start_item.setData(256, start)
            end_item = QTableWidgetItem(self._format_time(end))
            end_item.setData(256, end)
            text_item = QTableWidgetItem(str(segment["text"]))
            confidence_item = QTableWidgetItem(confidence_text)
            self.segment_table.setItem(row, 0, start_item)
            self.segment_table.setItem(row, 1, end_item)
            self.segment_table.setItem(row, 2, text_item)
            self.segment_table.setItem(row, 3, confidence_item)

    @staticmethod
    def _format_time(seconds: float) -> str:
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(int(minutes), 60)
        return f"{hours:02d}:{minutes:02d}:{sec:05.2f}"

    def play_selected_segment(self, _index=None) -> None:
        row = self.segment_table.currentRow()
        audio = Path(self.audio_path.text())
        if row < 0 or not audio.is_file():
            QMessageBox.warning(self, "Воспроизведение", "Выберите сегмент и существующий аудиофайл")
            return
        start = float(self.segment_table.item(row, 0).data(256))
        end = float(self.segment_table.item(row, 1).data(256))
        speed = float(self.playback_speed.currentData())
        self.player.setSource(QUrl.fromLocalFile(str(audio.resolve())))
        self.player.setPlaybackRate(speed)
        self.player.setPosition(round(start * 1000))
        self.player.play()
        self.play_stop_timer.start(max(100, round((end - start) * 1000 / speed)))

    def approve_transcript(self) -> None:
        assert self.lesson
        segment_texts = [
            self.segment_table.item(row, 2).text().strip()
            for row in range(self.segment_table.rowCount())
            if self.segment_table.item(row, 2) and self.segment_table.item(row, 2).text().strip()
        ]
        verified_text = " ".join(segment_texts) if segment_texts else self.transcript.toPlainText()
        self.transcript.setPlainText(verified_text)
        self.pipeline.approve_transcript(self.lesson, verified_text)
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
        worker.succeeded.connect(lambda result: QMessageBox.information(
            self, "Готово",
            f"Ветка: {result.branch}\nCommit: {result.commit[:12]}\nПуть: {result.repository_path}",
        ))
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(lambda: self.workers.remove(worker))
        self.workers.append(worker)
        worker.start()

    def _worker_failed(self, details: str) -> None:
        self.progress.setRange(0, 1)
        self.transcribe_button.setEnabled(True)
        self.publish_button.setEnabled(True)
        self.test_devices_button.setEnabled(True)
        logging.error(details)
        QMessageBox.critical(self, "Ошибка фоновой операции", details[-3000:])

    def _tick(self) -> None:
        self.recording_seconds += 1
        hours, remainder = divmod(self.recording_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.duration.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        if self.recorder and self.recorder.active:
            levels = self.recorder.levels
            self.mic_level.setValue(round(levels.microphone * 100))
            self.system_level.setValue(round(levels.system * 100))


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
