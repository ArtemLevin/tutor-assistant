from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig, load_students
from ..domain import JobStatus, Lesson
from ..pipeline import LessonPipeline
from ..recording import (
    DualRecorder,
    find_recoverable_recordings,
    list_input_devices,
    recover_recording,
    test_input_device,
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
        self._loading_segments = False
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.play_stop_timer = QTimer(self)
        self.play_stop_timer.setSingleShot(True)
        self.play_stop_timer.timeout.connect(self.player.pause)
        self.latex_poll_timer = QTimer(self)
        self.latex_poll_timer.setInterval(self.config.latex.poll_seconds * 1000)
        self.latex_poll_timer.timeout.connect(self.scan_remote_latex)
        self.setWindowTitle("Tutor Assistant")
        self.resize(1000, 720)
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.draft_timer = QTimer(self)
        self.draft_timer.setSingleShot(True)
        self.draft_timer.setInterval(1000)
        self.draft_timer.timeout.connect(self._save_transcript_draft)
        QTimer.singleShot(0, self._offer_recovery)
        QTimer.singleShot(100, self._offer_unfinished_job)
        QTimer.singleShot(
            0,
            lambda: self.auto_latex.setChecked(self.config.latex.enabled and self.config.latex.auto_monitor),
        )

    def _build(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._lesson_tab(), "1. Занятие")
        tabs.addTab(self._transcript_tab(), "2. Транскрипт")
        tabs.addTab(self._publish_tab(), "3. Публикация")
        tabs.addTab(self._latex_tab(), "4. PDF")
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

    def _offer_unfinished_job(self) -> None:
        active = [
            lesson
            for lesson in self.pipeline.store.list()
            if lesson.status not in {JobStatus.COMPLETED, JobStatus.FAILED}
        ]
        if not active:
            return
        lesson = active[0]
        answer = QMessageBox.question(
            self,
            "Незавершённое занятие",
            f"{lesson.student.full_name}\n{lesson.topic}\nЭтап: {lesson.status.value}\n\nПродолжить работу?",
        )
        if answer == QMessageBox.Yes:
            self._load_lesson(lesson)

    def _load_lesson(self, lesson: Lesson) -> None:
        self.lesson = lesson
        index = self.student.findData(lesson.student.id)
        if index >= 0:
            self.student.setCurrentIndex(index)
        index = self.subject.findText(lesson.subject)
        if index >= 0:
            self.subject.setCurrentIndex(index)
        self.topic.setText(lesson.topic)
        self.lesson_date.setDate(
            QDate(lesson.lesson_date.year, lesson.lesson_date.month, lesson.lesson_date.day)
        )
        if lesson.source_audio_local and Path(lesson.source_audio_local).exists():
            self.audio_path.setText(lesson.source_audio_local)
        if lesson.artifacts.verified_transcript and Path(lesson.artifacts.verified_transcript).exists():
            self.transcript.setPlainText(
                Path(lesson.artifacts.verified_transcript).read_text(encoding="utf-8")
            )
        if lesson.artifacts.segments_json and Path(lesson.artifacts.segments_json).exists():
            self._load_segments(Path(lesson.artifacts.segments_json))
            self._restore_transcript_draft()
        self.approve.setEnabled(lesson.status == JobStatus.REVIEW_REQUIRED)
        self.publish_button.setEnabled(lesson.status == JobStatus.READY)
        if lesson.status in {
            JobStatus.PUBLISHED,
            JobStatus.GENERATED_TEX,
            JobStatus.COMPILING_PDF,
            JobStatus.COMPILE_FAILED,
            JobStatus.PDF_REVIEW_REQUIRED,
        }:
            self.latex_monitor_status.setText(f"Восстановлено занятие: {lesson.status.value}")
        self.open_pr_button.setEnabled(bool(lesson.publication and lesson.publication.pr_url))
        self.statusBar().showMessage(f"Занятие восстановлено: {lesson.student.full_name}")

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
        for combo, configured in (
            (self.mic, self.config.recording.mic_device),
            (self.loopback, self.config.recording.loopback_device),
        ):
            index = combo.findData(configured)
            if index >= 0:
                combo.setCurrentIndex(index)
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
        self.recording_health_label = QLabel("Очереди: 0% / 0%; потеряно блоков: 0")
        diagnostics_layout.addRow("Состояние записи", self.recording_health_label)
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
        layout.addWidget(
            QLabel(
                "Проверьте числа, формулы и спорные фрагменты. "
                "После подтверждения текст попадёт в репозиторий ученика."
            )
        )
        self.segment_table = QTableWidget(0, 5)
        self.segment_table.setHorizontalHeaderLabels(["Начало", "Конец", "Говорящий", "Текст", "Уверенность"])
        self.segment_table.horizontalHeader().setStretchLastSection(False)
        self.segment_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.segment_table.setAlternatingRowColors(True)
        self.segment_table.doubleClicked.connect(self.play_selected_segment)
        self.segment_table.itemChanged.connect(lambda _item: self._schedule_draft_save())
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
        self.open_pr_button = QPushButton("Открыть draft PR")
        self.open_pr_button.setEnabled(False)
        self.open_pr_button.clicked.connect(self._open_current_pr)
        layout.addWidget(self.publish_summary)
        layout.addWidget(self.publish_button)
        layout.addWidget(self.open_pr_button)
        layout.addStretch()
        return page

    def _latex_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        environment = QGroupBox("Локальная LaTeX-среда")
        environment_layout = QHBoxLayout(environment)
        self.latex_doctor_button = QPushButton("Проверить TeX Live")
        self.latex_doctor_button.clicked.connect(self.latex_doctor)
        self.latex_environment_label = QLabel("Проверка ещё не выполнялась")
        environment_layout.addWidget(self.latex_doctor_button)
        environment_layout.addWidget(self.latex_environment_label, 1)
        layout.addWidget(environment)

        source_row = QHBoxLayout()
        self.tex_path = QLineEdit()
        self.tex_path.setPlaceholderText("Путь к полученному от ChatGPT .tex")
        choose = QPushButton("Выбрать TEX")
        choose.clicked.connect(self.choose_tex)
        self.compile_tex_button = QPushButton("Скомпилировать PDF")
        self.compile_tex_button.clicked.connect(self.compile_local_tex)
        source_row.addWidget(self.tex_path, 1)
        source_row.addWidget(choose)
        source_row.addWidget(self.compile_tex_button)
        layout.addLayout(source_row)

        monitor_row = QHBoxLayout()
        self.auto_latex = QCheckBox("Автоматически проверять ветки занятий")
        self.auto_latex.toggled.connect(self.toggle_latex_monitor)
        scan = QPushButton("Проверить сейчас")
        scan.clicked.connect(self.scan_remote_latex)
        self.latex_monitor_status = QLabel("Мониторинг выключен")
        monitor_row.addWidget(self.auto_latex)
        monitor_row.addWidget(scan)
        monitor_row.addWidget(self.latex_monitor_status, 1)
        layout.addLayout(monitor_row)

        self.compilation_log = QPlainTextEdit()
        self.compilation_log.setReadOnly(True)
        self.compilation_log.setPlaceholderText("Здесь появится журнал компиляции и понятное описание ошибок")
        layout.addWidget(self.compilation_log, 3)
        layout.addWidget(QLabel("Предпросмотр страниц — двойной клик открывает изображение:"))
        self.pdf_previews = QListWidget()
        self.pdf_previews.itemDoubleClicked.connect(
            lambda item: QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(256)))
        )
        layout.addWidget(self.pdf_previews, 2)
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
                self.config.recording.queue_blocks,
                self.config.recording.target_sample_rate,
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
        channels = self.config.recording.channels

        def run_tests():
            mic = test_input_device(mic_device, seconds, None, channels)
            system = test_input_device(loopback_device, seconds, None, channels)
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
        self._loading_segments = True
        self.segment_table.setRowCount(len(segments))
        for row, segment in enumerate(segments):
            start = float(segment["start"])
            end = float(segment["end"])
            confidence = segment.get("avg_logprob")
            confidence_text = (
                "—" if confidence is None else f"{min(100, max(0, round((1 + float(confidence)) * 100)))}%"
            )
            start_item = QTableWidgetItem(self._format_time(start))
            start_item.setData(256, start)
            end_item = QTableWidgetItem(self._format_time(end))
            end_item.setData(256, end)
            text_item = QTableWidgetItem(str(segment["text"]))
            speaker_item = QTableWidgetItem(str(segment.get("speaker") or "—"))
            confidence_item = QTableWidgetItem(confidence_text)
            self.segment_table.setItem(row, 0, start_item)
            self.segment_table.setItem(row, 1, end_item)
            self.segment_table.setItem(row, 2, speaker_item)
            self.segment_table.setItem(row, 3, text_item)
            self.segment_table.setItem(row, 4, confidence_item)
        self._loading_segments = False

    def _draft_path(self) -> Path | None:
        if not self.lesson:
            return None
        return self.pipeline.lesson_dir(self.lesson) / "transcript" / "transcript_draft.json"

    def _schedule_draft_save(self) -> None:
        if not self._loading_segments and self.lesson:
            self.draft_timer.start()

    def _save_transcript_draft(self) -> None:
        path = self._draft_path()
        if not path:
            return
        rows = []
        for row in range(self.segment_table.rowCount()):
            rows.append(
                {
                    "start": self.segment_table.item(row, 0).data(256),
                    "end": self.segment_table.item(row, 1).data(256),
                    "speaker": self.segment_table.item(row, 2).text(),
                    "text": self.segment_table.item(row, 3).text(),
                }
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _restore_transcript_draft(self) -> None:
        path = self._draft_path()
        if not path or not path.exists():
            return
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._loading_segments = True
        for row, item in enumerate(rows[: self.segment_table.rowCount()]):
            self.segment_table.item(row, 2).setText(str(item.get("speaker", "—")))
            self.segment_table.item(row, 3).setText(str(item.get("text", "")))
        self._loading_segments = False

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
            (
                f"[{self.segment_table.item(row, 2).text()}] "
                if self.segment_table.item(row, 2) and self.segment_table.item(row, 2).text() not in {"", "—"}
                else ""
            )
            + self.segment_table.item(row, 3).text().strip()
            for row in range(self.segment_table.rowCount())
            if self.segment_table.item(row, 3) and self.segment_table.item(row, 3).text().strip()
        ]
        verified_text = " ".join(segment_texts) if segment_texts else self.transcript.toPlainText()
        self.transcript.setPlainText(verified_text)
        self.pipeline.approve_transcript(self.lesson, verified_text)
        draft = self._draft_path()
        if draft and draft.exists():
            draft.unlink()
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
        worker.succeeded.connect(self._publication_ready)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(lambda: self.workers.remove(worker))
        self.workers.append(worker)
        worker.start()

    def _publication_ready(self, result) -> None:
        details = f"Ветка: {result.branch}\nCommit: {result.commit[:12]}\nПуть: {result.repository_path}"
        if result.pr_url:
            details += f"\nDraft PR: {result.pr_url}"
            self.open_pr_button.setEnabled(True)
        if result.warnings:
            details += "\n\n" + "\n".join(result.warnings)
        QMessageBox.information(self, "Готово", details)
        self.latex_monitor_status.setText("Ветка занятия опубликована; ожидаю handbook/*.tex")

    def _open_current_pr(self) -> None:
        if self.lesson and self.lesson.publication and self.lesson.publication.pr_url:
            QDesktopServices.openUrl(QUrl(self.lesson.publication.pr_url))

    def latex_doctor(self) -> None:
        from ..latex import inspect_latex_environment

        report = inspect_latex_environment(self.config.latex)
        if report.ready:
            message = f"Готово: latexmk={report.latexmk}, engine={report.engine}"
        else:
            message = "; ".join(report.messages) or "LaTeX-среда не готова"
        self.latex_environment_label.setText(message)
        QMessageBox.information(self, "Проверка TeX Live", message)

    def choose_tex(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "LaTeX-пособие", "", "LaTeX (*.tex)")
        if path:
            self.tex_path.setText(path)

    def compile_local_tex(self) -> None:
        from ..latex import LatexCompiler

        path = Path(self.tex_path.text())
        if not path.is_file():
            QMessageBox.warning(self, "Компиляция", "Выберите существующий TEX-файл")
            return
        self.compile_tex_button.setEnabled(False)
        self.compilation_log.setPlainText("Компиляция запущена…")
        worker = Worker(LatexCompiler(self.config.latex).compile, path)
        worker.succeeded.connect(self._local_compilation_ready)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(lambda: self.workers.remove(worker))
        self.workers.append(worker)
        worker.start()

    def _local_compilation_ready(self, result) -> None:
        self.compile_tex_button.setEnabled(True)
        try:
            log = result.log_file.read_text(encoding="utf-8")
        except OSError:
            log = "\n".join(result.errors + result.warnings)
        title = "PDF создан" if result.success else "Компиляция завершилась с ошибкой"
        summary = [title]
        if result.pdf_file:
            summary.append(f"PDF: {result.pdf_file}")
            summary.append(f"Страниц: {result.pages}; размер: {result.size_bytes} байт")
        summary.extend(f"Ошибка: {item}" for item in result.errors)
        summary.extend(f"Предупреждение: {item}" for item in result.warnings)
        self.compilation_log.setPlainText("\n".join(summary) + "\n\n" + log[-12000:])
        self.pdf_previews.clear()
        for path in result.preview_files:
            item = QListWidgetItem(path.name)
            item.setData(256, str(path.resolve()))
            self.pdf_previews.addItem(item)
        QMessageBox.information(self, "Компиляция", title)

    def toggle_latex_monitor(self, enabled: bool) -> None:
        if enabled:
            self.latex_poll_timer.start()
            self.latex_monitor_status.setText(f"Проверка каждые {self.config.latex.poll_seconds} секунд")
            self.scan_remote_latex()
        else:
            self.latex_poll_timer.stop()
            self.latex_monitor_status.setText("Мониторинг выключен")

    def scan_remote_latex(self) -> None:
        from ..latex import RemoteLatexService

        if any(getattr(worker, "purpose", "") == "latex-monitor" for worker in self.workers):
            return
        self.latex_monitor_status.setText("Проверяю удалённые ветки…")

        def scan():
            service = RemoteLatexService(self.config.repository, self.config.latex)
            for lesson in self.pipeline.store.list():
                if service.is_ready(lesson):
                    return service.compile_lesson(
                        lesson, cache_dir=self.pipeline.lesson_dir(lesson) / "latex-cache"
                    )
            return None

        worker = Worker(scan)
        worker.purpose = "latex-monitor"
        worker.succeeded.connect(self._remote_compilation_ready)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(lambda: self.workers.remove(worker))
        self.workers.append(worker)
        worker.start()

    def _remote_compilation_ready(self, remote_result) -> None:
        if remote_result is None:
            self.latex_monitor_status.setText("Новых TEX-файлов нет")
            return
        lesson = remote_result.lesson
        self.pipeline.store.save(lesson)
        lesson.write_json(self.pipeline.lesson_dir(lesson) / "lesson.json")
        result = remote_result.compilation
        if result.success:
            message = f"PDF создан и отправлен в {remote_result.branch}"
        else:
            message = (
                f"Компиляция не удалась, попытка {lesson.latex.attempt}/{self.config.latex.max_attempts}. "
                "В ветку добавлен reports/latex/latex_fix_request.md"
            )
        self.latex_monitor_status.setText(message)
        self.compilation_log.setPlainText("\n".join(result.errors + result.warnings) or message)
        self.pdf_previews.clear()
        for path in result.preview_files:
            item = QListWidgetItem(path.name)
            item.setData(256, str(path.resolve()))
            self.pdf_previews.addItem(item)
        QMessageBox.information(self, "Автоматическая компиляция", message)

    def _worker_failed(self, details: str) -> None:
        self.progress.setRange(0, 1)
        self.transcribe_button.setEnabled(True)
        self.publish_button.setEnabled(True)
        self.test_devices_button.setEnabled(True)
        self.compile_tex_button.setEnabled(True)
        logging.error(details)
        QMessageBox.critical(self, "Ошибка фоновой операции", details[-3000:])

    def _tick(self) -> None:
        self.recording_seconds += 1
        hours, remainder = divmod(self.recording_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.duration.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        if self.recorder and self.recorder.active:
            levels = self.recorder.levels
            health = self.recorder.health
            self.mic_level.setValue(round(levels.microphone * 100))
            self.system_level.setValue(round(levels.system * 100))
            dropped = health.microphone_dropped_blocks + health.system_dropped_blocks
            self.recording_health_label.setText(
                f"Очереди: {health.microphone_queue_percent}% / {health.system_queue_percent}%; "
                f"потеряно блоков: {dropped}; задержка writer: {health.max_writer_latency_ms:.1f} мс"
            )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    force_setup = "--setup" in sys.argv
    if force_setup:
        sys.argv.remove("--setup")
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/app.yaml")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    config = AppConfig.load(config_path)
    if force_setup or not config.setup_completed:
        from .setup_wizard import SetupWizard

        wizard = SetupWizard(config, config_path)
        if wizard.exec() != QDialog.Accepted:
            raise SystemExit(0)
    window = MainWindow(config_path)
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
