from __future__ import annotations

import json
import logging
import queue
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from time import sleep

from PySide6.QtCore import QDate, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
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
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..atomic_io import atomic_write_text
from ..config import AppConfig, load_students
from ..content import ContentMaintenanceResult
from ..content_browser import is_audio_path
from ..crm import CrmStore
from ..domain import JobStatus, Lesson
from ..logging_config import configure_logging, install_exception_hook, log_directory
from ..pipeline import LessonPipeline
from ..playback import PlaybackController, PlaybackSegment
from ..publisher import publication_payload_files
from ..quick_start import evaluate_readiness, selected_profile
from ..recording import (
    DualRecorder,
    SystemAudioSource,
    find_recoverable_recordings,
    list_input_devices,
    list_system_audio_sources,
    recover_recording,
)
from ..transcript_editing import select_verified_text
from ..transcription_queue import QueueStatus, TranscriptionQueue
from .crm import SchedulePage, StudentsPage
from .parallel_review import ParallelReviewPolicy
from .playback import QtPlaybackBackend, QtStopScheduler
from .student_content import StudentContentPage
from .theme import apply_theme, refresh_style, set_button_kind, set_status


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


class TranscriptionWorker(QThread):
    succeeded = Signal(str, object)
    failed = Signal(str, str)
    became_idle = Signal()

    def __init__(self, pipeline: LessonPipeline) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.pending: queue.Queue[tuple[str, Lesson, Path] | None] = queue.Queue()
        self.busy = False
        self._shutdown_sent = False

    def submit(self, job_id: str, lesson: Lesson, audio: Path) -> None:
        if self._shutdown_sent:
            raise RuntimeError("Поток транскрибации завершает работу")
        self.pending.put((job_id, lesson, audio))

    def shutdown(self) -> None:
        if not self._shutdown_sent:
            self._shutdown_sent = True
            self.pending.put(None)

    def run(self) -> None:
        while True:
            item = self.pending.get()
            if item is None:
                self.pending.task_done()
                return
            job_id, lesson, audio = item
            self.busy = True
            try:
                self.succeeded.emit(job_id, self.pipeline.transcribe(lesson, audio))
            except Exception:
                self.failed.emit(job_id, traceback.format_exc())
            finally:
                self.busy = False
                self.pending.task_done()
                self.became_idle.emit()


class MainWindow(QMainWindow):
    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config_path = config_path
        self.config = AppConfig.load(config_path)
        self.pipeline = LessonPipeline(self.config)
        self.students = load_students(self.config.students_file)
        self.crm_store = CrmStore(self.pipeline.store.path)
        self.crm_store.sync_students(self.students)
        self.students = self.crm_store.domain_students()
        self.content_service = self.pipeline.content_service
        self.devices = list_input_devices()
        self.system_sources = list_system_audio_sources(
            self.devices, self.config.recording.target_sample_rate
        )
        self.lesson: Lesson | None = None
        self.recording_lesson: Lesson | None = None
        self.recorder: DualRecorder | None = None
        self._recording_lease = None
        self.preflight_passed = False
        self.preflight_result = None
        self._recording_stop_started = False
        self._active_audio_warning = ""
        self._quick_start_pending = False
        self._quick_auto_transcribe_active = False
        self._quick_countdown_remaining = 0
        self._scheduled_occurrence_id: int | None = None
        self.recording_seconds = 0
        self.workers: list[Worker] = []
        self._recovery_sessions: list[Path] = []
        self.transcription_queue = TranscriptionQueue(self.pipeline.store)
        self._loading_segments = False
        self._summary_dirty = False
        self._shutdown_requested = False
        self._shutdown_ready = False
        self.transcription_worker = TranscriptionWorker(self.pipeline)
        self.transcription_worker.succeeded.connect(self._background_transcription_ready)
        self.transcription_worker.failed.connect(self._background_transcription_failed)
        self.transcription_worker.became_idle.connect(self._maybe_finish_shutdown)
        self.transcription_worker.finished.connect(self._maybe_finish_shutdown)
        self.playback_backend = QtPlaybackBackend(self)
        self.playback_scheduler = QtStopScheduler(self)
        self.playback_controller = PlaybackController(
            self.playback_backend,
            self.playback_scheduler,
            lambda: self._parallel_policy().audio_playback_allowed,
            self._playback_error,
        )
        self.playback_backend.error_occurred.connect(
            self.playback_controller.report_backend_error
        )
        self.quick_countdown_timer = QTimer(self)
        self.quick_countdown_timer.setInterval(1000)
        self.quick_countdown_timer.timeout.connect(self._quick_countdown_tick)
        self.latex_poll_timer = QTimer(self)
        self.latex_poll_timer.setInterval(self.config.latex.poll_seconds * 1000)
        self.latex_poll_timer.timeout.connect(self.scan_remote_latex)
        self.setWindowTitle("Tutor Assistant — рабочее пространство преподавателя")
        self.setMinimumSize(1040, 720)
        self.resize(1180, 820)
        self._build()
        self.transcription_worker.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.draft_timer = QTimer(self)
        self.draft_timer.setSingleShot(True)
        self.draft_timer.setInterval(1000)
        self.draft_timer.timeout.connect(self._save_transcript_draft)
        self.content_maintenance_timer = QTimer(self)
        self.content_maintenance_timer.setInterval(
            self.config.content.maintenance_interval_minutes * 60 * 1000
        )
        self.content_maintenance_timer.timeout.connect(self._run_content_maintenance)
        if self.config.content.maintenance_enabled:
            self.content_maintenance_timer.start()
            QTimer.singleShot(1000, self._run_content_maintenance)
        QTimer.singleShot(0, self._offer_recovery)
        QTimer.singleShot(100, self._restore_background_jobs)
        QTimer.singleShot(150, self._offer_unfinished_job)
        QTimer.singleShot(
            0,
            lambda: self.auto_latex.setChecked(self.config.latex.enabled and self.config.latex.auto_monitor),
        )

    def _build(self) -> None:
        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(24, 22, 24, 16)
        shell_layout.setSpacing(14)

        self.header = QFrame()
        self.header.setObjectName("appHeader")
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(22, 16, 22, 16)
        self.header_layout.setSpacing(18)
        self.brand_mark = QLabel("TA")
        self.brand_mark.setObjectName("brandMark")
        self.brand_mark.setAlignment(Qt.AlignCenter)
        self.header_layout.addWidget(self.brand_mark, 0, Qt.AlignVCenter)
        brand = QVBoxLayout()
        brand.setSpacing(2)
        self.header_eyebrow = QLabel("ЛОКАЛЬНОЕ РАБОЧЕЕ ПРОСТРАНСТВО")
        self.header_eyebrow.setObjectName("eyebrow")
        self.header_title = QLabel("Tutor Assistant")
        self.header_title.setObjectName("appTitle")
        self.header_subtitle = QLabel(
            "Запись занятия, проверка транскрипта и выпуск материалов в одном окне"
        )
        self.header_subtitle.setObjectName("subtitle")
        brand.addWidget(self.header_eyebrow)
        brand.addWidget(self.header_title)
        brand.addWidget(self.header_subtitle)
        self.header_layout.addLayout(brand, 1)
        self.app_status = QLabel()
        self.app_status.setObjectName("statusPill")
        self.app_status.setAlignment(Qt.AlignCenter)
        self.header_layout.addWidget(self.app_status, 0, Qt.AlignVCenter)
        self.support_button = set_button_kind(QPushButton("Собрать диагностику"), "ghost")
        self.support_button.setToolTip("Создать ZIP без аудио и транскриптов")
        self.support_button.clicked.connect(self._create_support_bundle)
        self.header_layout.addWidget(self.support_button, 0, Qt.AlignVCenter)
        self.logs_button = set_button_kind(QPushButton("Журнал"), "ghost")
        self.logs_button.setToolTip("Открыть каталог с журналами приложения")
        self.logs_button.clicked.connect(self._open_logs)
        self.header_layout.addWidget(self.logs_button, 0, Qt.AlignVCenter)
        self.quick_mode_button = set_button_kind(QPushButton("Быстрый урок"), "primary")
        self.quick_mode_button.setToolTip("Вернуться к минимальному экрану записи")
        self.quick_mode_button.clicked.connect(lambda: self._set_mode("quick"))
        self.header_layout.addWidget(self.quick_mode_button, 0, Qt.AlignVCenter)
        self.detailed_mode_button = set_button_kind(QPushButton("Расширенный режим"), "ghost")
        self.detailed_mode_button.setToolTip("Открыть все настройки и этапы обработки")
        self.detailed_mode_button.clicked.connect(lambda: self._set_mode("detailed"))
        self.header_layout.addWidget(self.detailed_mode_button, 0, Qt.AlignVCenter)
        shell_layout.addWidget(self.header)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.addTab(self._lesson_tab(), "01  Занятие")
        self.tabs.addTab(self._transcript_tab(), "02  Транскрипт")
        self.tabs.addTab(self._publish_tab(), "03  Публикация")
        self.tabs.addTab(self._latex_tab(), "04  PDF")
        self.tabs.addTab(self._processing_tab(), "05  Обработка")
        self.crm_students_page = StudentsPage(self.crm_store)
        self.crm_schedule_page = SchedulePage(self.crm_store)
        self.crm_students_page.changed.connect(self._crm_students_changed)
        self.crm_students_page.changed.connect(self.crm_schedule_page.refresh)
        self.crm_students_page.materials_requested.connect(self._open_student_materials)
        self.crm_schedule_page.start_requested.connect(self._start_scheduled_lesson)
        self.tabs.addTab(self.crm_students_page, "06  Ученики")
        self.tabs.addTab(self.crm_schedule_page, "07  Расписание")
        self.student_content_page = StudentContentPage(
            self.content_service,
            self.students,
            self._run_content_task,
            self.playback_controller,
            self.playback_backend,
        )
        self.student_content_page.status_changed.connect(self._set_status)
        self.student_content_page.file_open_requested.connect(self._open_material_file)
        self.student_content_page.audio_queue_requested.connect(self._queue_imported_audio)
        self.student_content_page.lesson_trashed.connect(self._forget_trashed_lesson)
        self.student_content_page.lesson_purged.connect(self._forget_trashed_lesson)
        self.student_content_page.trash_retention_changed.connect(self._save_trash_retention)
        self.materials_tab_index = self.tabs.addTab(
            self.student_content_page, "08  Материалы"
        )
        self.content_stack = QStackedWidget()
        self.quick_page = self._quick_start_page()
        self.content_stack.addWidget(self.quick_page)
        self.content_stack.addWidget(self.tabs)
        shell_layout.addWidget(self.content_stack, 1)
        self.setCentralWidget(shell)
        self.statusBar().setSizeGripEnabled(False)
        self._set_status("Готово к работе")
        self._set_mode("quick" if self.config.quick_start.start_in_quick_mode else "detailed")

    @staticmethod
    def _page_heading(title: str, description: str) -> QWidget:
        heading = QWidget()
        layout = QVBoxLayout(heading)
        layout.setContentsMargins(2, 2, 2, 4)
        layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        description_label = QLabel(description)
        description_label.setObjectName("subtitle")
        description_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(description_label)
        return heading

    def _set_status(self, message: str, tone: str = "success") -> None:
        set_status(self.app_status, message, tone)
        self.statusBar().showMessage(message)
        if hasattr(self, "header_title"):
            self.header_title.setToolTip(message)

    def _go_to(self, index: int) -> None:
        self._set_mode("detailed")
        self.tabs.setCurrentIndex(index)

    def _set_mode(self, mode: str) -> None:
        quick = mode == "quick"
        self.content_stack.setCurrentIndex(0 if quick else 1)
        self.support_button.setVisible(not quick)
        self.logs_button.setVisible(not quick)
        self.app_status.setVisible(not quick)
        self.header_eyebrow.setVisible(not quick)
        self.header_subtitle.setVisible(not quick)
        self.quick_mode_button.setVisible(not quick)
        self.statusBar().setVisible(not quick)
        if quick:
            self.header_layout.setContentsMargins(16, 10, 16, 10)
            self.setMinimumSize(720, 520)
        else:
            self.header_layout.setContentsMargins(22, 16, 22, 16)
            self.setMinimumSize(1040, 720)
        self.header_layout.setSpacing(12 if quick else 18)
        self.detailed_mode_button.setText("•••" if quick else "Расширенный режим")
        self.detailed_mode_button.setFixedWidth(44 if quick else 170)
        self.detailed_mode_button.setToolTip(
            "Расширенный режим: настройки, транскрипт и публикация"
            if quick
            else "Открыть все настройки и этапы обработки"
        )
        set_button_kind(self.quick_mode_button, "primary" if quick else "ghost")
        set_button_kind(self.detailed_mode_button, "ghost" if quick else "primary")
        refresh_style(self.quick_mode_button)
        refresh_style(self.detailed_mode_button)
        if quick:
            self._refresh_quick_readiness()

    def _open_logs(self) -> None:
        directory = log_directory(self.config.workspace)
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve())))

    def _crm_students_changed(self) -> None:
        self.students = self.crm_store.domain_students()
        for combo in (self.student, self.quick_student):
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for item in self.students:
                combo.addItem(item.full_name, item.id)
            index = combo.findData(selected)
            if index >= 0:
                combo.setCurrentIndex(index)
            combo.blockSignals(False)
        self._refresh_quick_readiness()
        self.student_content_page.set_students(self.students)

    def _run_content_task(self, callable_, succeeded, failed) -> None:
        worker = Worker(callable_)
        worker.purpose = "content-browser"
        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _run_content_maintenance(self) -> None:
        if (
            not self.config.content.maintenance_enabled
            or self._shutdown_requested
            or (self.recorder and self.recorder.active)
            or any(
                getattr(worker, "purpose", "") == "content-maintenance"
                for worker in self.workers
            )
        ):
            return

        def maintain() -> ContentMaintenanceResult:
            return self.content_service.run_maintenance(
                auto_repair=self.config.content.auto_repair,
                purge_expired=self.config.content.auto_purge_trash,
                cleanup_temporary=self.config.content.auto_cleanup_temporary,
                temporary_retention=timedelta(
                    hours=self.config.content.temporary_retention_hours
                ),
                backup_enabled=self.config.content.backup_enabled,
                backup_interval=timedelta(
                    hours=self.config.content.backup_interval_hours
                ),
                backup_retention_count=self.config.content.backup_retention_count,
            )

        worker = Worker(maintain)
        worker.purpose = "content-maintenance"
        worker.succeeded.connect(self._content_maintenance_ready)
        worker.failed.connect(self._content_maintenance_failed)
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _content_maintenance_ready(self, result: object) -> None:
        if not isinstance(result, ContentMaintenanceResult):
            self._content_maintenance_failed("Некорректный результат обслуживания архива")
            return
        logging.info(
            "Результат фонового обслуживания архива: %s",
            result.model_dump(mode="json"),
        )
        self.student_content_page.refresh_if_loaded()
        if result.errors:
            self._set_status(
                f"Архив обслужен с предупреждениями · ошибок {len(result.errors)}",
                "warning",
            )

    def _content_maintenance_failed(self, details: str) -> None:
        logging.error("Фоновое обслуживание архива завершилось ошибкой: %s", details)
        self._set_status("Ошибка фонового обслуживания архива", "warning")

    def _open_student_materials(self, student_id: str) -> None:
        self.student_content_page.show_student(student_id)
        self._go_to(self.materials_tab_index)

    def _open_material_file(self, path: Path) -> None:
        if is_audio_path(path):
            self.student_content_page.playback_panel.play_path(path)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _queue_imported_audio(self, lesson: Lesson, audio: Path) -> None:
        if not audio.is_file():
            self._set_status("Импортированное аудио не найдено", "error")
            return
        self._enqueue_transcription(lesson, audio)
        self._set_status(
            f"{lesson.student.full_name}: импорт добавлен в очередь",
            "working",
        )

    def _forget_trashed_lesson(self, lesson_id: str) -> None:
        try:
            self.transcription_queue.discard(lesson_id)
        except ValueError:
            logging.warning("Активное задание не удалено из очереди: %s", lesson_id)
        if self.lesson and self.lesson.lesson_id == lesson_id:
            self.lesson = None
            self._prepare_next_lesson()
        self._update_transcription_queue_ui()

    def _save_trash_retention(self, days: int) -> None:
        self.config.content.trash_retention_days = days
        self.config.save(self.config_path)

    def _parallel_policy(self) -> ParallelReviewPolicy:
        return ParallelReviewPolicy(
            recording_active=bool(self.recorder and self.recorder.active),
            recording_stopping=self._recording_stop_started,
        )

    def _playback_error(self, message: str) -> None:
        logging.warning("Ошибка воспроизведения: %s", message)
        self._set_status(message, "error")

    def _start_scheduled_lesson(
        self,
        occurrence_id: int,
        student_id: str,
        subject: str,
        topic: str,
    ) -> None:
        if self._recording_stop_started or (self.recorder and self.recorder.active):
            QMessageBox.warning(self, "Расписание", "Сначала завершите текущую запись")
            return
        student_index = self.quick_student.findData(student_id)
        if student_index < 0:
            QMessageBox.warning(self, "Расписание", "Ученик отсутствует в активных карточках")
            return
        self.quick_student.setCurrentIndex(student_index)
        subject_index = self.quick_subject.findText(subject)
        if subject_index >= 0:
            self.quick_subject.setCurrentIndex(subject_index)
        self.quick_topic.setText(topic.strip() or subject)
        self._scheduled_occurrence_id = occurrence_id
        self._set_mode("quick")
        QTimer.singleShot(0, self._quick_start_clicked)

    def _update_scheduled_occurrence(
        self,
        status: str,
        *,
        lesson_id: str | None = None,
        clear: bool = False,
    ) -> None:
        occurrence_id = self._scheduled_occurrence_id
        if occurrence_id is None:
            return
        try:
            self.crm_store.update_occurrence(
                occurrence_id,
                status=status,
                lesson_id=lesson_id,
            )
            self.crm_schedule_page.refresh()
        except Exception:
            logging.exception("Не удалось обновить занятие в расписании")
        finally:
            if clear:
                self._scheduled_occurrence_id = None

    def _create_support_bundle(self) -> None:
        from ..support import create_support_bundle

        self.support_button.setEnabled(False)
        self._set_status("Собираю диагностический пакет…", "working")
        worker = Worker(create_support_bundle, self.config, self.config_path)
        worker.succeeded.connect(self._support_bundle_ready)
        worker.failed.connect(lambda details: self._operation_failed("support", details))
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _support_bundle_ready(self, path: Path) -> None:
        self.support_button.setEnabled(True)
        self._set_status("Диагностический пакет создан")
        QMessageBox.information(
            self,
            "Диагностика",
            f"ZIP создан без аудио и транскриптов:\n{path}",
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _worker_finished(self, worker: Worker) -> None:
        if worker in self.workers:
            self.workers.remove(worker)
        self._maybe_finish_shutdown()

    def _offer_recovery(self) -> None:
        self._recovery_sessions = list(
            reversed(find_recoverable_recordings(self.config.workspace))
        )
        self._offer_next_recovery()

    def _offer_next_recovery(self) -> None:
        if not self._recovery_sessions:
            return
        directory = self._recovery_sessions.pop(0)
        answer = QMessageBox.question(
            self,
            "Незавершённая запись",
            f"Найдены сохранённые чанки:\n{directory}\n\nВосстановить аудиозапись?",
        )
        if answer != QMessageBox.Yes:
            self._offer_next_recovery()
            return
        self._set_status("Восстанавливаю аудиозапись…", "working")
        worker = Worker(recover_recording, directory)
        worker.succeeded.connect(self._recovery_ready)
        worker.failed.connect(self._recovery_failed)
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _recovery_ready(self, result) -> None:
        self.audio_path.setText(str(result.mixed_file))
        try:
            session = json.loads(result.session_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            session = {}
        session["status"] = "recovered"
        atomic_write_text(
            result.session_file,
            json.dumps(session, ensure_ascii=False, indent=2),
        )
        self._set_status("Аудиозапись восстановлена")
        QMessageBox.information(self, "Восстановление", f"Запись восстановлена:\n{result.mixed_file}")
        self._offer_next_recovery()

    def _recovery_failed(self, details: str) -> None:
        self._operation_failed("recovery", details)
        self._offer_next_recovery()

    def _offer_unfinished_job(self) -> None:
        active = [
            lesson
            for lesson in self.pipeline.store.list()
            if lesson.status
            not in {
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.RECORDED,
                JobStatus.TRANSCRIBING,
            }
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

    def _restore_background_jobs(self) -> None:
        restored = 0
        lessons = {lesson.lesson_id: lesson for lesson in self.pipeline.store.list(limit=1000)}
        for stored in self.pipeline.store.list_transcription_jobs():
            lesson = lessons.get(stored.lesson_id)
            if lesson is None:
                continue
            try:
                status = QueueStatus(stored.status)
            except ValueError:
                continue
            audio = Path(stored.audio_path)
            if status in {QueueStatus.WAITING, QueueStatus.RUNNING} and not audio.is_file():
                continue
            self.transcription_queue.restore(lesson, audio, status, stored.error)
            restored += 1
        known = {job.id for job in self.transcription_queue.jobs}
        for lesson in reversed(tuple(lessons.values())):
            if lesson.status not in {JobStatus.RECORDED, JobStatus.TRANSCRIBING}:
                continue
            if lesson.lesson_id in known:
                continue
            if not lesson.source_audio_local:
                continue
            audio = Path(lesson.source_audio_local)
            if not audio.is_file():
                continue
            self.transcription_queue.enqueue(lesson, audio)
            restored += 1
        if restored:
            self._update_transcription_queue_ui()
            self._pump_transcription_queue()
            self._set_status(f"Восстановлена история обработки · {restored}", "working")

    def _load_lesson(self, lesson: Lesson) -> None:
        self.lesson = lesson
        self._loading_segments = True
        self._summary_dirty = False
        self.audio_path.clear()
        self.transcript.clear()
        self.segment_table.setRowCount(0)
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
        self._loading_segments = False
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
        if lesson.status == JobStatus.REVIEW_REQUIRED:
            self._go_to(1)
        elif lesson.status == JobStatus.READY:
            self._go_to(2)
        elif lesson.status in {
            JobStatus.PUBLISHED,
            JobStatus.GENERATED_TEX,
            JobStatus.COMPILING_PDF,
            JobStatus.COMPILE_FAILED,
            JobStatus.PDF_REVIEW_REQUIRED,
        }:
            self._go_to(3)
        self._set_status(f"Занятие восстановлено · {lesson.student.full_name}")

    def _quick_start_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("quickPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 28)
        layout.setSpacing(0)

        self.quick_profile = QComboBox()
        self.quick_profile.setToolTip("Набор настроек быстрого запуска")
        for profile in self.config.quick_start.profiles:
            self.quick_profile.addItem(profile.name, profile.id)
        profile_index = self.quick_profile.findData(self.config.quick_start.default_profile_id)
        if profile_index >= 0:
            self.quick_profile.setCurrentIndex(profile_index)
        self.quick_student = QComboBox()
        for item in self.students:
            self.quick_student.addItem(item.full_name, item.id)
        profile = selected_profile(self.config, self.quick_profile.currentData())
        student_id = self.config.quick_start.last_student_id or profile.student_id
        student_index = self.quick_student.findData(student_id)
        if student_index >= 0:
            self.quick_student.setCurrentIndex(student_index)
        self.quick_student.setToolTip("Выберите ученика для нового занятия")
        self.quick_subject = QComboBox()
        self.quick_subject.setToolTip("Предмет определяет папку и шаблоны материалов")
        self.quick_subject.addItems(["mathematics", "physics", "chemistry"])
        subject = self.config.quick_start.last_subject or profile.subject
        subject_index = self.quick_subject.findText(subject)
        if subject_index >= 0:
            self.quick_subject.setCurrentIndex(subject_index)
        self.quick_topic = QLineEdit(self.config.quick_start.last_topic)
        self.quick_topic.setPlaceholderText("Тема занятия")
        self.quick_topic.setToolTip("Кратко укажите тему — она попадёт в карточку занятия")

        surface = QFrame()
        surface.setObjectName("quickSurface")
        surface.setMaximumWidth(610)
        surface_layout = QVBoxLayout(surface)
        surface_layout.setContentsMargins(26, 24, 26, 26)
        surface_layout.setSpacing(12)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        quick_title = QLabel("Новый урок")
        quick_title.setObjectName("quickTitle")
        quick_title.setToolTip("Быстрая запись с фоновой транскрибацией")
        top_row.addWidget(quick_title, 1)

        self.quick_readiness_button = QPushButton("✓")
        self.quick_readiness_button.setObjectName("quickStatusButton")
        self.quick_readiness_button.clicked.connect(self._show_readiness_dialog)
        top_row.addWidget(self.quick_readiness_button)

        self.quick_options_button = QPushButton("···")
        self.quick_options_button.setObjectName("quickIconButton")
        self.quick_options_button.setToolTip("Профиль и предмет")
        self.quick_options_button.clicked.connect(self._show_quick_options_dialog)
        top_row.addWidget(self.quick_options_button)
        surface_layout.addLayout(top_row)
        surface_layout.addSpacing(2)
        surface_layout.addWidget(self.quick_student)
        surface_layout.addWidget(self.quick_topic)

        self.quick_start_button = set_button_kind(QPushButton("Начать занятие"), "primary")
        self.quick_start_button.setObjectName("quickStartButton")
        self.quick_start_button.setMinimumHeight(58)
        self.quick_start_button.setShortcut(QKeySequence("F9"))
        self.quick_start_button.setToolTip("Начать или завершить быстрый урок · F9")
        self.quick_start_button.clicked.connect(self._quick_start_clicked)
        surface_layout.addWidget(self.quick_start_button)

        self.quick_queue_button = QPushButton("≡ 0")
        self.quick_queue_button.setObjectName("quickQueueButton")
        self.quick_queue_button.setToolTip(
            "Очередь фоновой транскрибации пуста\nНажмите, чтобы открыть обработку"
        )
        self.quick_queue_button.clicked.connect(self._show_processing_queue)
        surface_layout.addWidget(self.quick_queue_button, 0, Qt.AlignCenter)

        layout.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(surface, 1)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        self.quick_profile.currentIndexChanged.connect(self._apply_quick_profile)
        self.quick_student.currentIndexChanged.connect(self._refresh_quick_readiness)
        self.quick_subject.currentIndexChanged.connect(self._refresh_quick_readiness)
        self.quick_topic.textChanged.connect(self._refresh_quick_readiness)
        self._refresh_quick_readiness()
        return page

    def _show_quick_options_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Параметры быстрого урока")
        dialog.setModal(True)
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        title = QLabel("Параметры урока")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        profile = QComboBox()
        profile.setToolTip("Профиль хранит настройки запуска и автоматизации")
        for index in range(self.quick_profile.count()):
            profile.addItem(self.quick_profile.itemText(index), self.quick_profile.itemData(index))
        profile.setCurrentIndex(self.quick_profile.currentIndex())

        subject = QComboBox()
        subject.setToolTip("Предмет определяет папку ученика и используемые шаблоны")
        for index in range(self.quick_subject.count()):
            subject.addItem(self.quick_subject.itemText(index), self.quick_subject.itemData(index))
        subject.setCurrentIndex(self.quick_subject.currentIndex())

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        form.addRow("Профиль", profile)
        form.addRow("Предмет", subject)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return
        self.quick_profile.setCurrentIndex(profile.currentIndex())
        self.quick_subject.setCurrentIndex(subject.currentIndex())

    def _apply_quick_profile(self) -> None:
        profile = selected_profile(self.config, self.quick_profile.currentData())
        if profile.student_id:
            index = self.quick_student.findData(profile.student_id)
            if index >= 0:
                self.quick_student.setCurrentIndex(index)
        index = self.quick_subject.findText(profile.subject)
        if index >= 0:
            self.quick_subject.setCurrentIndex(index)
        self._refresh_quick_readiness()

    def _refresh_quick_readiness(self) -> None:
        if not hasattr(self, "quick_readiness_button"):
            return
        readiness = evaluate_readiness(
            self.config,
            self.students,
            self.devices,
            self.system_sources,
            self.quick_student.currentData(),
            self.quick_topic.text(),
        )
        self.quick_readiness_button.setText("✓" if readiness.ready else "!")
        self.quick_readiness_button.setProperty("tone", "ready" if readiness.ready else "blocked")
        lines = [
            f"{'✓' if item.ready else '!'} {item.label}: {item.detail}" for item in readiness.items
        ]
        lines.append("")
        lines.append("Нажмите, чтобы открыть подробную проверку")
        self.quick_readiness_button.setToolTip("\n".join(lines))
        refresh_style(self.quick_readiness_button)
        if not self.quick_countdown_timer.isActive() and not (self.recorder and self.recorder.active):
            self.quick_start_button.setText("Начать занятие")
            self.quick_start_button.setEnabled(readiness.ready)

    def _show_readiness_dialog(self) -> None:
        readiness = evaluate_readiness(
            self.config,
            self.students,
            self.devices,
            self.system_sources,
            self.quick_student.currentData(),
            self.quick_topic.text(),
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("Готовность к старту")
        dialog.setModal(True)
        dialog.setMinimumWidth(520)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(22, 20, 22, 18)
        dialog_layout.setSpacing(10)
        title = QLabel("Готовность к старту")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Проверяем только то, что требуется для записи занятия")
        subtitle.setObjectName("muted")
        dialog_layout.addWidget(title)
        dialog_layout.addWidget(subtitle)
        dialog_layout.addSpacing(5)
        for item in readiness.items:
            item_frame = QFrame()
            item_frame.setObjectName("readinessItem")
            item_layout = QHBoxLayout(item_frame)
            item_layout.setContentsMargins(13, 10, 13, 10)
            mark = QLabel("✓" if item.ready else "!")
            mark.setObjectName("readinessMark")
            mark.setProperty("tone", "ready" if item.ready else "blocked")
            text = QLabel(f"{item.label}\n{item.detail}")
            text.setWordWrap(True)
            item_layout.addWidget(mark, 0, Qt.AlignTop)
            item_layout.addWidget(text, 1)
            dialog_layout.addWidget(item_frame)
        close_button = set_button_kind(QPushButton("Закрыть"), "primary")
        close_button.clicked.connect(dialog.accept)
        dialog_layout.addWidget(close_button, 0, Qt.AlignRight)
        dialog.exec()

    def _sync_quick_to_lesson(self) -> None:
        student_index = self.student.findData(self.quick_student.currentData())
        if student_index >= 0:
            self.student.setCurrentIndex(student_index)
        subject_index = self.subject.findText(self.quick_subject.currentText())
        if subject_index >= 0:
            self.subject.setCurrentIndex(subject_index)
        self.topic.setText(self.quick_topic.text().strip())
        self.lesson_date.setDate(QDate.currentDate())
        self.config.quick_start.default_profile_id = str(self.quick_profile.currentData())
        self.config.quick_start.last_student_id = self.quick_student.currentData()
        self.config.quick_start.last_subject = self.quick_subject.currentText()
        self.config.quick_start.last_topic = self.quick_topic.text().strip()
        self.config.save(self.config_path)

    def _quick_start_clicked(self) -> None:
        if self.quick_countdown_timer.isActive():
            self._cancel_quick_countdown()
            return
        if self.recorder and self.recorder.active:
            self.quick_start_button.setEnabled(False)
            self.stop_recording()
            return
        readiness = evaluate_readiness(
            self.config,
            self.students,
            self.devices,
            self.system_sources,
            self.quick_student.currentData(),
            self.quick_topic.text(),
        )
        if not readiness.ready:
            QMessageBox.warning(
                self,
                "Быстрый запуск",
                "\n".join(item.detail for item in readiness.blockers),
            )
            return
        self._sync_quick_to_lesson()
        profile = selected_profile(self.config, self.quick_profile.currentData())
        self._quick_auto_transcribe_active = profile.auto_transcribe
        if self.preflight_passed:
            self._start_quick_countdown(profile.countdown_seconds)
            return
        self._quick_start_pending = True
        self.quick_start_button.setEnabled(False)
        self.quick_start_button.setText("Проверяю аудио…")
        self._begin_preflight(show_intro=False)

    def _start_quick_countdown(self, seconds: int) -> None:
        self._quick_start_pending = False
        self._quick_countdown_remaining = max(1, seconds)
        self.quick_start_button.setEnabled(True)
        self.quick_start_button.setText(f"Отменить запуск · {self._quick_countdown_remaining}")
        self._set_status("Аудио готово · запуск через несколько секунд", "working")
        self.quick_countdown_timer.start()

    def _quick_countdown_tick(self) -> None:
        self._quick_countdown_remaining -= 1
        if self._quick_countdown_remaining > 0:
            self.quick_start_button.setText(f"Отменить запуск · {self._quick_countdown_remaining}")
            return
        self.quick_countdown_timer.stop()
        self.quick_start_button.setText("Завершить занятие")
        self.quick_start_button.setEnabled(True)
        self.start_recording()

    def _cancel_quick_countdown(self) -> None:
        self.quick_countdown_timer.stop()
        self._quick_start_pending = False
        self._quick_auto_transcribe_active = False
        self._update_scheduled_occurrence("planned", clear=True)
        self._set_status("Быстрый запуск отменён", "warning")
        self._refresh_quick_readiness()

    def _lesson_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(12)
        layout.addWidget(
            self._page_heading(
                "Подготовьте занятие",
                "Укажите контекст, проверьте оба источника звука и запустите запись.",
            )
        )

        columns = QHBoxLayout()
        columns.setSpacing(12)
        form_box = QGroupBox("Параметры занятия")
        form = QFormLayout(form_box)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(11)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
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
        for source in self.system_sources:
            self.loopback.addItem(source.display_name, source)
        if not self.system_sources:
            self.loopback.addItem("WASAPI Loopback-устройства не найдены", None)
            self.loopback.setEnabled(False)
        mic_index = self.mic.findData(self.config.recording.mic_device)
        if mic_index >= 0:
            self.mic.setCurrentIndex(mic_index)
        self._select_system_source()
        self.mic.currentIndexChanged.connect(lambda _index: self._persist_audio_selection())
        self.loopback.currentIndexChanged.connect(lambda _index: self._persist_audio_selection())
        form.addRow("Ученик", self.student)
        form.addRow("Предмет", self.subject)
        form.addRow("Тема", self.topic)
        form.addRow("Дата", self.lesson_date)
        form.addRow("Микрофон", self.mic)
        form.addRow("Системный звук / loopback", self.loopback)
        columns.addWidget(form_box, 3)

        diagnostics = QGroupBox("Уровни и стабильность")
        diagnostics_layout = QFormLayout(diagnostics)
        diagnostics_layout.setVerticalSpacing(13)
        self.mic_level = QProgressBar()
        self.mic_level.setRange(0, 100)
        self.mic_level.setTextVisible(False)
        self.system_level = QProgressBar()
        self.system_level.setRange(0, 100)
        self.system_level.setTextVisible(False)
        diagnostics_layout.addRow("Микрофон", self.mic_level)
        diagnostics_layout.addRow("Системный звук", self.system_level)
        self.recording_health_label = QLabel("Очереди: 0% / 0%; потеряно блоков: 0")
        self.recording_health_label.setObjectName("muted")
        self.recording_health_label.setWordWrap(True)
        diagnostics_layout.addRow("Состояние записи", self.recording_health_label)
        self.test_devices_button = set_button_kind(QPushButton("Проверить оба устройства"), "ghost")
        self.test_devices_button.clicked.connect(self.test_devices)
        diagnostics_layout.addRow(self.test_devices_button)
        preflight_controls = QHBoxLayout()
        self.play_mic_test_button = set_button_kind(QPushButton("Прослушать микрофон"), "ghost")
        self.play_system_test_button = set_button_kind(QPushButton("Прослушать звук ученика"), "ghost")
        self.play_mic_test_button.setEnabled(False)
        self.play_system_test_button.setEnabled(False)
        self.play_mic_test_button.clicked.connect(lambda: self._play_preflight_track("microphone"))
        self.play_system_test_button.clicked.connect(lambda: self._play_preflight_track("system"))
        preflight_controls.addWidget(self.play_mic_test_button)
        preflight_controls.addWidget(self.play_system_test_button)
        diagnostics_layout.addRow(preflight_controls)
        columns.addWidget(diagnostics, 2)
        layout.addLayout(columns)

        recording = QGroupBox("Запись и транскрибация")
        recording_layout = QVBoxLayout(recording)
        recording_layout.setSpacing(12)
        recording_header = QHBoxLayout()
        timer_block = QVBoxLayout()
        timer_block.setSpacing(1)
        self.recording_state_label = QLabel("ГОТОВО К ЗАПИСИ")
        self.recording_state_label.setObjectName("recordingState")
        self.duration = QLabel("00:00:00")
        self.duration.setObjectName("timerDisplay")
        timer_block.addWidget(self.recording_state_label)
        timer_block.addWidget(self.duration)
        recording_header.addLayout(timer_block)
        recording_header.addStretch()
        self.start_button = set_button_kind(QPushButton("Начать запись"), "primary")
        self.stop_button = set_button_kind(QPushButton("Завершить"), "danger")
        self.start_button.setShortcut(QKeySequence("Ctrl+R"))
        self.start_button.setToolTip("Начать запись · Ctrl+R")
        self.stop_button.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.stop_button.setToolTip("Завершить запись · Ctrl+Shift+R")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_recording)
        self.stop_button.clicked.connect(self.stop_recording)
        recording_header.addWidget(self.start_button)
        recording_header.addWidget(self.stop_button)
        recording_layout.addLayout(recording_header)

        audio_row = QHBoxLayout()
        self.audio_path = QLineEdit()
        self.audio_path.setPlaceholderText("Путь появится после записи или выберите готовый файл")
        choose = set_button_kind(QPushButton("Выбрать аудио"), "ghost")
        choose.clicked.connect(self.choose_audio)
        audio_row.addWidget(self.audio_path, 1)
        audio_row.addWidget(choose)
        recording_layout.addLayout(audio_row)
        action_row = QHBoxLayout()
        action_row.addStretch()
        self.transcribe_button = set_button_kind(QPushButton("Запустить локальную транскрибацию"), "primary")
        self.transcribe_button.setShortcut(QKeySequence("Ctrl+T"))
        self.transcribe_button.setToolTip("Запустить транскрибацию · Ctrl+T")
        self.transcribe_button.clicked.connect(self.transcribe)
        action_row.addWidget(self.transcribe_button)
        recording_layout.addLayout(action_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setTextVisible(False)
        recording_layout.addWidget(self.progress)
        layout.addWidget(recording)
        layout.addStretch()
        return page

    def _select_system_source(self) -> None:
        configured_id = self.config.recording.system_device_id
        configured_backend = self.config.recording.system_backend
        for index in range(self.loopback.count()):
            source = self.loopback.itemData(index)
            if not isinstance(source, SystemAudioSource):
                continue
            matches_current = source.device_id == configured_id and source.backend == configured_backend
            matches_legacy = (
                configured_id is None
                and source.legacy_index is not None
                and source.legacy_index == self.config.recording.loopback_device
            )
            if matches_current or matches_legacy:
                self.loopback.setCurrentIndex(index)
                return

    def _persist_audio_selection(self) -> None:
        if self.mic.currentData() is not None:
            self.config.recording.mic_device = int(self.mic.currentData())
        source = self.loopback.currentData()
        if isinstance(source, SystemAudioSource):
            self.config.recording.system_device_id = source.device_id
            self.config.recording.system_backend = source.backend
            self.config.recording.loopback_device = source.legacy_index
        self.preflight_passed = False
        self.preflight_result = None
        self.config.save(self.config_path)
        self._refresh_quick_readiness()

    def _transcript_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(12)
        layout.addWidget(
            self._page_heading(
                "Проверьте транскрипт",
                "Исправьте формулы, числа и имена. Двойной клик по строке воспроизводит фрагмент аудио.",
            )
        )

        segments = QGroupBox("Сегменты распознавания")
        segments_layout = QVBoxLayout(segments)
        segments_layout.setSpacing(10)
        self.segment_table = QTableWidget(0, 5)
        self.segment_table.setHorizontalHeaderLabels(["Начало", "Конец", "Говорящий", "Текст", "Уверенность"])
        self.segment_table.horizontalHeader().setStretchLastSection(False)
        self.segment_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.segment_table.verticalHeader().setVisible(False)
        self.segment_table.setShowGrid(False)
        self.segment_table.setAlternatingRowColors(True)
        self.segment_table.doubleClicked.connect(self.play_selected_segment)
        self.segment_table.itemChanged.connect(lambda _item: self._schedule_draft_save())
        segments_layout.addWidget(self.segment_table, 4)
        controls = QHBoxLayout()
        self.play_segment_button = set_button_kind(QPushButton("▶  Воспроизвести сегмент"), "ghost")
        self.play_segment_button.clicked.connect(self.play_selected_segment)
        self.playback_speed = QComboBox()
        self.playback_speed.setFixedWidth(92)
        for label, value in [("0,75×", 0.75), ("1×", 1.0), ("1,25×", 1.25)]:
            self.playback_speed.addItem(label, value)
        controls.addWidget(self.play_segment_button)
        speed_label = QLabel("Скорость")
        speed_label.setObjectName("muted")
        controls.addWidget(speed_label)
        controls.addWidget(self.playback_speed)
        controls.addStretch()
        segments_layout.addLayout(controls)
        summary = QGroupBox("Сводный текст")
        summary_layout = QVBoxLayout(summary)
        self.transcript = QPlainTextEdit()
        self.transcript.setPlaceholderText("Здесь появится распознанный текст занятия")
        self.transcript.setMinimumHeight(210)
        self.transcript.textChanged.connect(self._summary_changed)
        summary_layout.addWidget(self.transcript, 1)
        approve_row = QHBoxLayout()
        hint = QLabel("Подтверждённая версия будет опубликована в папке ученика")
        hint.setObjectName("muted")
        approve_row.addWidget(hint, 1)
        self.approve = set_button_kind(QPushButton("Подтвердить транскрипт"), "primary")
        self.approve.setShortcut(QKeySequence("Ctrl+Return"))
        self.approve.setToolTip("Подтвердить транскрипт · Ctrl+Enter")
        self.approve.setEnabled(False)
        self.approve.clicked.connect(self.approve_transcript)
        approve_row.addWidget(self.approve)
        summary_layout.addLayout(approve_row)
        transcript_splitter = QSplitter(Qt.Vertical)
        transcript_splitter.setChildrenCollapsible(False)
        transcript_splitter.addWidget(segments)
        transcript_splitter.addWidget(summary)
        transcript_splitter.setStretchFactor(0, 3)
        transcript_splitter.setStretchFactor(1, 2)
        transcript_splitter.setSizes([390, 290])
        layout.addWidget(transcript_splitter, 1)
        return page

    def _publish_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(12)
        layout.addWidget(
            self._page_heading(
                "Опубликуйте материалы",
                "Приложение создаст изолированную ветку занятия и draft pull request для проверки.",
            )
        )
        layout.addStretch(1)
        card_row = QHBoxLayout()
        card_row.addStretch(1)
        card = QGroupBox("Готовность задания")
        card.setMaximumWidth(720)
        card.setMinimumWidth(560)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(14)
        intro = QLabel("Публикация станет доступна после подтверждения транскрипта")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        card_layout.addWidget(intro)
        summary_panel = QFrame()
        summary_panel.setObjectName("infoPanel")
        summary_panel_layout = QVBoxLayout(summary_panel)
        summary_panel_layout.setContentsMargins(16, 14, 16, 14)
        self.publish_summary = QLabel("Сначала создайте и подтвердите транскрипт.")
        self.publish_summary.setWordWrap(True)
        summary_panel_layout.addWidget(self.publish_summary)
        card_layout.addWidget(summary_panel)
        actions = QHBoxLayout()
        actions.addStretch()
        self.open_pr_button = set_button_kind(QPushButton("Открыть draft PR"), "ghost")
        self.open_pr_button.setEnabled(False)
        self.open_pr_button.clicked.connect(self._open_current_pr)
        actions.addWidget(self.open_pr_button)
        self.publish_button = set_button_kind(QPushButton("Создать ветку и опубликовать"), "primary")
        self.publish_button.setEnabled(False)
        self.publish_button.clicked.connect(self.publish)
        actions.addWidget(self.publish_button)
        card_layout.addLayout(actions)
        card_row.addWidget(card)
        card_row.addStretch(1)
        layout.addLayout(card_row)
        layout.addStretch(2)
        return page

    def _processing_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(12)
        layout.addWidget(
            self._page_heading(
                "Фоновая обработка",
                "Записывайте следующие занятия, пока Whisper последовательно обрабатывает очередь.",
            )
        )
        summary = QFrame()
        summary.setObjectName("infoPanel")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(16, 12, 16, 12)
        self.processing_summary = QLabel("Очередь пуста")
        self.processing_summary.setObjectName("readinessSummary")
        summary_layout.addWidget(self.processing_summary, 1)
        back = set_button_kind(QPushButton("Новый урок"), "primary")
        back.clicked.connect(lambda: self._set_mode("quick"))
        summary_layout.addWidget(back)
        layout.addWidget(summary)
        self.processing_list = QListWidget()
        self.processing_list.setAlternatingRowColors(True)
        self.processing_list.setSpacing(3)
        self.processing_list.itemDoubleClicked.connect(self._open_processing_item)
        layout.addWidget(self.processing_list, 1)
        hint = QLabel("Двойной клик по готовому заданию открывает транскрипт для проверки")
        hint.setObjectName("muted")
        layout.addWidget(hint)
        return page

    def _latex_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(12)
        layout.addWidget(
            self._page_heading(
                "Соберите и проверьте PDF",
                "Безопасная локальная компиляция LaTeX, журнал ошибок и предпросмотр страниц.",
            )
        )
        environment = QGroupBox("Локальная LaTeX-среда")
        environment_layout = QHBoxLayout(environment)
        self.latex_doctor_button = set_button_kind(QPushButton("Проверить TeX Live"), "ghost")
        self.latex_doctor_button.clicked.connect(self.latex_doctor)
        self.latex_environment_label = QLabel("Проверка ещё не выполнялась")
        self.latex_environment_label.setObjectName("muted")
        self.latex_environment_label.setWordWrap(True)
        environment_layout.addWidget(self.latex_doctor_button)
        environment_layout.addWidget(self.latex_environment_label, 1)
        layout.addWidget(environment)

        source = QGroupBox("Исходный TEX")
        source_layout = QVBoxLayout(source)
        source_row = QHBoxLayout()
        self.tex_path = QLineEdit()
        self.tex_path.setPlaceholderText("Путь к полученному от ChatGPT .tex")
        choose = set_button_kind(QPushButton("Выбрать TEX"), "ghost")
        choose.clicked.connect(self.choose_tex)
        self.compile_tex_button = set_button_kind(QPushButton("Скомпилировать PDF"), "primary")
        self.compile_tex_button.clicked.connect(self.compile_local_tex)
        source_row.addWidget(self.tex_path, 1)
        source_row.addWidget(choose)
        source_row.addWidget(self.compile_tex_button)
        source_layout.addLayout(source_row)

        monitor_row = QHBoxLayout()
        self.auto_latex = QCheckBox("Автоматически проверять ветки занятий")
        self.auto_latex.toggled.connect(self.toggle_latex_monitor)
        scan = set_button_kind(QPushButton("Проверить сейчас"), "ghost")
        scan.clicked.connect(self.scan_remote_latex)
        self.latex_monitor_status = QLabel("Мониторинг выключен")
        self.latex_monitor_status.setObjectName("muted")
        self.latex_monitor_status.setWordWrap(True)
        monitor_row.addWidget(self.auto_latex)
        monitor_row.addWidget(scan)
        monitor_row.addWidget(self.latex_monitor_status, 1)
        source_layout.addLayout(monitor_row)
        layout.addWidget(source)

        results = QHBoxLayout()
        log_box = QGroupBox("Журнал компиляции")
        log_layout = QVBoxLayout(log_box)
        self.compilation_log = QPlainTextEdit()
        self.compilation_log.setReadOnly(True)
        self.compilation_log.setPlaceholderText("Здесь появится журнал компиляции и понятное описание ошибок")
        log_layout.addWidget(self.compilation_log)
        results.addWidget(log_box, 3)
        preview_box = QGroupBox("Предпросмотр страниц")
        preview_layout = QVBoxLayout(preview_box)
        preview_hint = QLabel("Двойной клик открывает страницу")
        preview_hint.setObjectName("muted")
        preview_layout.addWidget(preview_hint)
        self.pdf_previews = QListWidget()
        self.pdf_previews.itemDoubleClicked.connect(
            lambda item: QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(256)))
        )
        preview_layout.addWidget(self.pdf_previews)
        results.addWidget(preview_box, 2)
        layout.addLayout(results, 1)
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
            if self._shutdown_requested:
                raise RuntimeError("Приложение завершает фоновые операции")
            if self._recording_stop_started or (self.recorder and self.recorder.active):
                raise RuntimeError("Запись уже запущена или сохраняется")
            self.playback_controller.prepare_recording()
            if self.config.recording.require_preflight and not self.preflight_passed:
                answer = QMessageBox.question(
                    self,
                    "Проверка аудио",
                    "Тестовая запись ещё не прошла проверку. Продолжить занятие без неё?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if answer != QMessageBox.Yes:
                    return
            recording_lesson = self._make_lesson()
            self.recording_lesson = recording_lesson
            self._recording_lease = self.content_service.acquire_activity(
                "recording",
                lesson_id=recording_lesson.lesson_id,
                ttl=timedelta(minutes=5),
            )
            directory = self.pipeline.lesson_dir(recording_lesson) / "recording"
            self.recorder = DualRecorder(
                self.config.recording.sample_rate,
                self.config.recording.channels,
                self.config.recording.chunk_seconds,
                self.config.recording.queue_blocks,
                self.config.recording.target_sample_rate,
            )
            system_source = self.loopback.currentData()
            if not isinstance(system_source, SystemAudioSource):
                raise ValueError("Выберите устройство WASAPI Loopback для системного звука")
            recording_lesson.transition(JobStatus.RECORDING)
            self.pipeline.save_state(recording_lesson, "status", "error")
            self.recorder.start(directory, int(self.mic.currentData()), system_source)
            self._update_scheduled_occurrence("in_progress", lesson_id=recording_lesson.lesson_id)
            self.recording_seconds = 0
            self._recording_stop_started = False
            self._active_audio_warning = ""
            self.timer.start(1000)
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.test_devices_button.setEnabled(False)
            if self._quick_auto_transcribe_active:
                self.quick_start_button.setText("Завершить занятие")
                self.quick_start_button.setEnabled(True)
            self.recording_state_label.setText("●  ИДЁТ ЗАПИСЬ")
            self.recording_state_label.setProperty("active", True)
            refresh_style(self.recording_state_label)
            self._set_status("Идёт запись", "working")
            logging.info(
                "Запись начата: lesson=%s mic=%s system=%s",
                recording_lesson.lesson_id,
                self.mic.currentText(),
                system_source.display_name,
            )
        except Exception as exc:
            logging.exception("Не удалось начать запись")
            failed_lesson = self.recording_lesson
            if self.recorder and self.recorder.active:
                try:
                    self.recorder.stop()
                except Exception:
                    logging.exception("Не удалось остановить recorder после ошибки запуска")
            self.recorder = None
            self.recording_lesson = None
            self._release_recording_lease()
            self._update_scheduled_occurrence("planned", clear=True)
            if failed_lesson:
                try:
                    failed_lesson.transition(JobStatus.FAILED, str(exc))
                    self.pipeline.save_state(failed_lesson, "status", "error")
                except Exception:
                    logging.exception("Не удалось сохранить ошибку запуска записи")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.test_devices_button.setEnabled(True)
            self._quick_auto_transcribe_active = False
            self._refresh_quick_readiness()
            QMessageBox.critical(self, "Ошибка записи", str(exc))

    def stop_recording(self) -> None:
        self._stop_recording_async()

    def _stop_recording_async(self, reason: str | None = None) -> None:
        if self._recording_stop_started or not self.recorder or not self.recording_lesson:
            return
        recorder = self.recorder
        recording_lesson = self.recording_lesson
        self._recording_stop_started = True
        self.timer.stop()
        self.stop_button.setEnabled(False)
        self.recording_state_label.setText("СОХРАНЯЮ ЗАПИСЬ…")
        self._set_status("Сохраняю и проверяю аудиодорожки…", "working")
        if reason:
            logging.warning("Аварийное завершение записи: %s", reason)
        else:
            logging.info("Завершение записи запрошено")
        worker = Worker(recorder.stop)
        worker.purpose = "recording-stop"
        worker.succeeded.connect(
            lambda result, lesson=recording_lesson, source=recorder: self._recording_ready(
                result, lesson, source, reason
            )
        )
        worker.failed.connect(self._recording_stop_failed)
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _recording_ready(
        self,
        result,
        recorded_lesson: Lesson,
        source_recorder: DualRecorder,
        reason: str | None = None,
    ) -> None:
        try:
            self._recording_ready_impl(result, recorded_lesson, source_recorder, reason)
        except Exception:
            details = traceback.format_exc()
            logging.error("Ошибка финализации записанного занятия\n%s", details)
            try:
                recorded_lesson.transition(JobStatus.FAILED, details[-2000:])
                self.pipeline.save_state(recorded_lesson, "status", "error")
            except Exception:
                logging.exception("Не удалось сохранить состояние ошибки записи")
            self._recording_stop_started = False
            self.recorder = None
            self.recording_lesson = None
            self._update_scheduled_occurrence("recording_failed", clear=True)
            self.start_button.setEnabled(True)
            self.quick_start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.test_devices_button.setEnabled(True)
            self._quick_auto_transcribe_active = False
            self._set_status("Аудио сохранено, оформление занятия завершилось с ошибкой", "error")
            QMessageBox.critical(
                self,
                "Ошибка оформления записи",
                f"Аудиофайл сохранён: {result.mixed_file}\n\n{details[-2000:]}",
            )
            self._maybe_finish_shutdown()
        finally:
            self._release_recording_lease()

    def _release_recording_lease(self) -> None:
        if self._recording_lease is not None:
            self._recording_lease.release()
            self._recording_lease = None

    def _recording_ready_impl(
        self,
        result,
        recorded_lesson: Lesson,
        source_recorder: DualRecorder,
        reason: str | None = None,
    ) -> None:
        if self.recording_lesson is not recorded_lesson or self.recorder is not source_recorder:
            logging.error("Игнорируется результат записи с устаревшим контекстом")
            return
        recorded_lesson.source_audio_local = str(result.mixed_file.resolve())
        recorded_lesson.transition(JobStatus.RECORDED)
        self.pipeline.save_state(
            recorded_lesson,
            "source_audio_local",
            "status",
            "error",
        )
        self._update_scheduled_occurrence(
            "completed",
            lesson_id=recorded_lesson.lesson_id,
            clear=True,
        )
        self.start_button.setEnabled(True)
        self.quick_start_button.setEnabled(True)
        self.test_devices_button.setEnabled(True)
        self.quick_start_button.setText("Начать занятие")
        self.recording_state_label.setText("ЗАПИСЬ СОХРАНЕНА")
        self.recording_state_label.setProperty("active", False)
        refresh_style(self.recording_state_label)
        quality = json.loads(result.quality_report.read_text(encoding="utf-8"))
        warnings = list(quality.get("warnings", []))
        if reason:
            warnings.insert(0, reason)
        if warnings:
            self._set_status("Запись сохранена с предупреждениями", "warning")
            QMessageBox.warning(self, "Проверка записи", "\n".join(warnings))
        else:
            self._set_status("Запись сохранена и проверена")
        logging.info("Запись сохранена: %s; quality_ready=%s", result.mixed_file, quality.get("ready"))
        self._recording_stop_started = False
        self.recorder = None
        self.recording_lesson = None
        if self._quick_auto_transcribe_active:
            self._quick_auto_transcribe_active = False
            self._enqueue_transcription(recorded_lesson, result.mixed_file)
            self._prepare_next_lesson()
            self._set_status(
                f"{recorded_lesson.student.full_name}: транскрибация в фоне · можно начинать следующий урок",
                "working",
            )
        else:
            self.lesson = recorded_lesson
            self.audio_path.setText(str(result.mixed_file))
            self._refresh_quick_readiness()
        self._maybe_finish_shutdown()

    def _prepare_next_lesson(self) -> None:
        self.recording_lesson = None
        if self.lesson is None:
            self.audio_path.clear()
        self.topic.clear()
        self.quick_topic.clear()
        self.config.quick_start.last_topic = ""
        self.config.save(self.config_path)
        self.duration.setText("00:00:00")
        self.recording_state_label.setText("ГОТОВО К ЗАПИСИ")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.transcribe_button.setEnabled(True)
        self.quick_start_button.setText("Начать занятие")
        self._refresh_quick_readiness()

    def _recording_stop_failed(self, details: str) -> None:
        logging.error(details)
        self._recording_stop_started = False
        self.recorder = None
        self.recording_lesson = None
        self._release_recording_lease()
        self._update_scheduled_occurrence("recording_failed", clear=True)
        self.start_button.setEnabled(True)
        self.test_devices_button.setEnabled(True)
        self._quick_auto_transcribe_active = False
        self._refresh_quick_readiness()
        self.stop_button.setEnabled(False)
        self.recording_state_label.setText("ЗАПИСЬ ТРЕБУЕТ ВОССТАНОВЛЕНИЯ")
        self.recording_state_label.setProperty("active", False)
        refresh_style(self.recording_state_label)
        self._set_status("Запись сохранена частично; доступно восстановление", "error")
        QMessageBox.critical(
            self,
            "Ошибка завершения записи",
            "Доступные аудиочанки сохранены. После перезапуска приложение предложит восстановление.\n\n"
            + details[-2000:],
        )
        self._maybe_finish_shutdown()

    def choose_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Аудиозапись", "", "Audio (*.wav *.mp3 *.m4a *.flac)")
        if path:
            self.audio_path.setText(path)
            self._set_status("Аудиофайл выбран")

    def test_devices(self) -> None:
        self._begin_preflight(show_intro=True)

    def _begin_preflight(self, show_intro: bool) -> None:
        mic_device = int(self.mic.currentData())
        system_source = self.loopback.currentData()
        if not isinstance(system_source, SystemAudioSource):
            self.test_devices_button.setEnabled(True)
            self._quick_start_pending = False
            self._refresh_quick_readiness()
            QMessageBox.warning(self, "Системный звук", "WASAPI Loopback-устройство не выбрано")
            return
        if show_intro:
            QMessageBox.information(
                self,
                "Тестовая запись",
                "После закрытия окна говорите в микрофон и одновременно воспроизводите звук через G733. "
                f"Запись продлится {self.config.recording.diagnostics_seconds} секунд.",
            )
        self.test_devices_button.setEnabled(False)
        self._set_status("Записываю тест микрофона и системного звука…", "working")
        logging.info("Тестовая запись начата: mic=%s system=%s", self.mic.currentText(), system_source.name)
        seconds = self.config.recording.diagnostics_seconds

        def run_tests():
            directory = self.config.workspace / "diagnostics" / datetime.now().strftime("%Y%m%d-%H%M%S")
            recorder = DualRecorder(
                self.config.recording.sample_rate,
                self.config.recording.channels,
                max(self.config.recording.chunk_seconds, seconds + 1),
                self.config.recording.queue_blocks,
                self.config.recording.target_sample_rate,
            )
            recorder.start(directory, mic_device, system_source)
            sleep(seconds)
            return recorder.stop()

        worker = Worker(run_tests)
        worker.succeeded.connect(self._device_test_ready)
        worker.failed.connect(lambda details: self._operation_failed("device-test", details))
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _device_test_ready(self, results) -> None:
        quality = json.loads(results.quality_report.read_text(encoding="utf-8"))
        mic = quality["microphone"]
        system = quality["system"]
        self.mic_level.setValue(round(min(1.0, float(mic["rms"]) * 5) * 100))
        self.system_level.setValue(round(min(1.0, float(system["rms"]) * 5) * 100))
        warnings = list(quality.get("warnings", []))
        self.preflight_passed = bool(quality.get("ready"))
        self.preflight_result = results
        self.play_mic_test_button.setEnabled(True)
        self.play_system_test_button.setEnabled(True)
        message = (
            "Тестовая запись прошла проверку. Прослушайте обе дорожки."
            if self.preflight_passed
            else "Проверка выявила проблемы: " + "; ".join(warnings)
        )
        if not self._quick_start_pending or not self.preflight_passed:
            QMessageBox.information(self, "Диагностика аудио", message)
        self.test_devices_button.setEnabled(True)
        self._set_status(message, "warning" if warnings else "success")
        logging.info(
            "Тестовая запись завершена: ready=%s report=%s", self.preflight_passed, results.quality_report
        )
        if self._quick_start_pending and self.preflight_passed:
            profile = selected_profile(self.config, self.quick_profile.currentData())
            self._start_quick_countdown(profile.countdown_seconds)
        elif self._quick_start_pending:
            self._quick_start_pending = False
            self._quick_auto_transcribe_active = False
            self._refresh_quick_readiness()

    def _play_preflight_track(self, source: str) -> None:
        if not self.preflight_result:
            return
        path = (
            self.preflight_result.microphone_file
            if source == "microphone"
            else self.preflight_result.system_file
        )
        if self.playback_controller.play_file(path, rate=1.0, start_ms=0):
            self._set_status(f"Воспроизвожу {path.name}", "working")

    def transcribe(self) -> None:
        try:
            if self.lesson is None or self.lesson.status not in {
                JobStatus.DRAFT,
                JobStatus.RECORDED,
                JobStatus.FAILED,
            }:
                self.lesson = self._make_lesson()
            audio = Path(self.audio_path.text())
            if not audio.is_file():
                raise ValueError("Выберите существующий аудиофайл")
            lesson = self.lesson
            lesson.source_audio_local = str(audio.resolve())
            if lesson.status == JobStatus.DRAFT:
                lesson.transition(JobStatus.RECORDED)
            elif lesson.status == JobStatus.FAILED:
                lesson.transition(JobStatus.RECORDED)
            self.pipeline.save_state(
                lesson,
                "source_audio_local",
                "status",
                "error",
            )
            self._enqueue_transcription(lesson, audio)
            self.lesson = None
            self._prepare_next_lesson()
            self._set_status(
                f"{lesson.student.full_name}: добавлено в фоновую очередь",
                "working",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", str(exc))

    def _enqueue_transcription(self, lesson: Lesson, audio: Path) -> None:
        job = self.transcription_queue.enqueue(lesson, audio)
        logging.info("Транскрибация поставлена в очередь: lesson=%s audio=%s", job.id, audio)
        self._update_transcription_queue_ui()
        self._pump_transcription_queue()

    def _pump_transcription_queue(self) -> None:
        if self._shutdown_requested:
            return
        job = self.transcription_queue.start_next()
        if job is None:
            return
        self._update_transcription_queue_ui()
        self.transcription_worker.submit(job.id, job.lesson, job.audio)

    def _background_transcription_ready(self, job_id: str, lesson: Lesson) -> None:
        self.transcription_queue.complete(job_id, lesson)
        self._update_transcription_queue_ui()
        self._set_status(f"Транскрипт готов · {lesson.student.full_name}", "warning")
        logging.info("Фоновая транскрибация завершена: lesson=%s", lesson.lesson_id)
        self._pump_transcription_queue()

    def _background_transcription_failed(self, job_id: str, details: str) -> None:
        job = self.transcription_queue.fail(job_id, details)
        self._update_transcription_queue_ui()
        logging.error("Фоновая транскрибация завершилась с ошибкой: lesson=%s\n%s", job_id, details)
        self._set_status(f"Ошибка транскрибации · {job.lesson.student.full_name}", "error")
        self._pump_transcription_queue()

    def _update_transcription_queue_ui(self) -> None:
        if not hasattr(self, "processing_list"):
            return
        labels = {
            QueueStatus.WAITING: "Ожидает",
            QueueStatus.RUNNING: "Транскрибируется",
            QueueStatus.READY: "Готов к проверке",
            QueueStatus.FAILED: "Ошибка",
        }
        self.processing_list.clear()
        for job in reversed(self.transcription_queue.jobs):
            item = QListWidgetItem(
                f"{labels[job.status]}  ·  {job.lesson.student.full_name}  ·  {job.lesson.topic}"
            )
            item.setData(256, job.id)
            if job.error:
                item.setToolTip(job.error[-1500:])
            self.processing_list.addItem(item)
        unfinished = self.transcription_queue.unfinished_count
        ready = sum(job.status == QueueStatus.READY for job in self.transcription_queue.jobs)
        self.processing_summary.setText(f"В обработке: {unfinished} · готовы к проверке: {ready}")
        self.quick_queue_button.setText(f"≡ {unfinished + ready}")
        self.quick_queue_button.setToolTip(
            f"В обработке: {unfinished}\n"
            f"Готовы к проверке: {ready}\n"
            "Нажмите, чтобы открыть очередь"
        )

    def _show_processing_queue(self) -> None:
        self._set_mode("detailed")
        self.tabs.setCurrentIndex(4)

    def _open_processing_item(self, item: QListWidgetItem) -> None:
        job = self.transcription_queue.get(str(item.data(256)))
        if job is None:
            return
        if (self.recorder and self.recorder.active) or self._recording_stop_started:
            QMessageBox.warning(
                self,
                "Идёт запись",
                "Завершите текущую запись перед открытием другого занятия.",
            )
            return
        if job.status == QueueStatus.FAILED:
            answer = QMessageBox.question(
                self,
                "Ошибка транскрибации",
                f"{job.error or 'Неизвестная ошибка'}\n\nПовторить транскрибацию?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                if not job.audio.is_file():
                    QMessageBox.critical(self, "Ошибка", f"Аудиофайл не найден: {job.audio}")
                    return
                job.lesson.transition(JobStatus.RECORDED, force=True)
                self.pipeline.save_state(
                    job.lesson,
                    "status",
                    "error",
                    force_status=True,
                )
                self.transcription_queue.retry(job.id)
                self._update_transcription_queue_ui()
                self._pump_transcription_queue()
            return
        if job.status != QueueStatus.READY:
            self._set_status("Транскрипт ещё обрабатывается", "working")
            return
        self._load_lesson(job.lesson)

    def _load_segments(self, path: Path) -> None:
        segments = json.loads(path.read_text(encoding="utf-8"))
        was_loading = self._loading_segments
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
        self._loading_segments = was_loading

    def _summary_changed(self) -> None:
        if self._loading_segments or not self.lesson:
            return
        self._summary_dirty = True
        self._schedule_draft_save()

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
        payload = {
            "segments": rows,
            "summary": self.transcript.toPlainText(),
            "summary_dirty": self._summary_dirty,
        }
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _restore_transcript_draft(self) -> None:
        path = self._draft_path()
        if not path or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, list):
            rows = payload
            summary = None
            summary_dirty = False
        else:
            rows = payload.get("segments", [])
            summary = payload.get("summary")
            summary_dirty = bool(payload.get("summary_dirty"))
        self._loading_segments = True
        for row, item in enumerate(rows[: self.segment_table.rowCount()]):
            self.segment_table.item(row, 2).setText(str(item.get("speaker", "—")))
            self.segment_table.item(row, 3).setText(str(item.get("text", "")))
        if summary is not None:
            self.transcript.setPlainText(str(summary))
        self._summary_dirty = summary_dirty
        self._loading_segments = False

    @staticmethod
    def _format_time(seconds: float) -> str:
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(int(minutes), 60)
        return f"{hours:02d}:{minutes:02d}:{sec:05.2f}"

    def play_selected_segment(self, _index=None) -> None:
        row = self.segment_table.currentRow()
        audio = Path(
            self.lesson.source_audio_local
            if self.lesson and self.lesson.source_audio_local
            else self.audio_path.text()
        )
        if row < 0 or not audio.is_file():
            QMessageBox.warning(self, "Воспроизведение", "Выберите сегмент и существующий аудиофайл")
            return
        start = float(self.segment_table.item(row, 0).data(256))
        end = float(self.segment_table.item(row, 1).data(256))
        speed = float(self.playback_speed.currentData())
        self.playback_controller.play_segment(
            audio,
            PlaybackSegment(start_seconds=start, end_seconds=end),
            rate=speed,
        )

    def approve_transcript(self) -> None:
        if not self.lesson or self.lesson.status != JobStatus.REVIEW_REQUIRED:
            QMessageBox.warning(self, "Транскрипт", "Выберите занятие, готовое к проверке")
            return
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
        verified_text = select_verified_text(
            segment_texts,
            self.transcript.toPlainText(),
            self._summary_dirty,
        )
        self._loading_segments = True
        self.transcript.setPlainText(verified_text)
        self._loading_segments = False
        self._summary_dirty = False
        self.pipeline.approve_transcript(self.lesson, verified_text)
        draft = self._draft_path()
        if draft and draft.exists():
            draft.unlink()
        payload = "\n".join(
            f"• {path}" for path in publication_payload_files(self.lesson)
        )
        self.publish_summary.setText(
            f"{self.lesson.student.full_name}\n{self.lesson.lesson_date:%d.%m.%Y}\n"
            f"{self.lesson.topic}\n\nБудут опубликованы:\n{payload}\n\n"
            "Задание будет помещено в отдельную Git-ветку."
        )
        self.publish_button.setEnabled(True)
        self._set_status("Транскрипт подтверждён")
        self._go_to(2)
        logging.info("Транскрипт подтверждён: lesson=%s", self.lesson.lesson_id)

    def publish(self) -> None:
        if not self.lesson or self.lesson.status != JobStatus.READY:
            QMessageBox.warning(self, "Публикация", "Сначала подтвердите транскрипт")
            return
        self.publish_button.setEnabled(False)
        self._set_status("Создаю ветку и публикую занятие…", "working")
        logging.info("Публикация начата: lesson=%s", self.lesson.lesson_id)
        worker = Worker(self.pipeline.publish, self.lesson)
        worker.succeeded.connect(self._publication_ready)
        worker.failed.connect(lambda details: self._operation_failed("publish", details))
        worker.finished.connect(lambda: self._worker_finished(worker))
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
        self._set_status("Занятие опубликовано")
        self._go_to(3)
        logging.info("Публикация завершена: branch=%s commit=%s", result.branch, result.commit)

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
        self._set_status(message, "success" if report.ready else "warning")
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
        self._set_status("Компилирую PDF…", "working")
        logging.info("Локальная компиляция LaTeX начата: %s", path)
        def compile_tex():
            with self.content_service.activity("latex-compilation"):
                return LatexCompiler(self.config.latex).compile(path)

        worker = Worker(compile_tex)
        worker.succeeded.connect(self._local_compilation_ready)
        worker.failed.connect(lambda details: self._operation_failed("compile", details))
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _local_compilation_ready(self, result) -> None:
        self.compile_tex_button.setEnabled(True)
        self.support_button.setEnabled(True)
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
        self._set_status(title, "success" if result.success else "error")
        QMessageBox.information(self, "Компиляция", title)

    def toggle_latex_monitor(self, enabled: bool) -> None:
        if enabled:
            self.latex_poll_timer.start()
            self.latex_monitor_status.setText(f"Проверка каждые {self.config.latex.poll_seconds} секунд")
            self._set_status("Автомониторинг LaTeX включён", "working")
            self.scan_remote_latex()
        else:
            self.latex_poll_timer.stop()
            self.latex_monitor_status.setText("Мониторинг выключен")
            self._set_status("Автомониторинг LaTeX выключен")

    def scan_remote_latex(self) -> None:
        from ..latex import RemoteLatexService

        if any(getattr(worker, "purpose", "") == "latex-monitor" for worker in self.workers):
            return
        self.latex_monitor_status.setText("Проверяю удалённые ветки…")
        self._set_status("Проверяю ветки занятий…", "working")

        def scan():
            with self.content_service.activity("latex-monitor"):
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
        worker.failed.connect(lambda details: self._operation_failed("latex-monitor", details))
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _remote_compilation_ready(self, remote_result) -> None:
        if remote_result is None:
            self.latex_monitor_status.setText("Новых TEX-файлов нет")
            self._set_status("Новых TEX-файлов нет")
            return
        lesson = remote_result.lesson
        self.pipeline.save_state(
            lesson,
            "latex",
            "status",
            "error",
            force_status=True,
        )
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
        self._set_status(message, "success" if result.success else "warning")
        QMessageBox.information(self, "Автоматическая компиляция", message)

    def _operation_failed(self, purpose: str, details: str) -> None:
        if purpose == "support":
            self.support_button.setEnabled(True)
        elif purpose == "device-test":
            self.test_devices_button.setEnabled(True)
            self._quick_start_pending = False
            self._quick_auto_transcribe_active = False
            self.quick_countdown_timer.stop()
            self._refresh_quick_readiness()
        elif purpose == "publish":
            self.publish_button.setEnabled(bool(self.lesson and self.lesson.status == JobStatus.READY))
        elif purpose == "compile":
            self.compile_tex_button.setEnabled(True)
        elif purpose == "latex-monitor":
            self.latex_monitor_status.setText("Ошибка проверки удалённых TEX-файлов")
        logging.error(details)
        self._set_status(f"Ошибка фоновой операции · {purpose}", "error")
        QMessageBox.critical(self, "Ошибка фоновой операции", details[-3000:])

    def closeEvent(self, event: QCloseEvent) -> None:
        self.playback_controller.stop(clear_source=True)
        if self._shutdown_ready:
            event.accept()
            return
        has_recording = bool(self.recorder and self.recorder.active) or self._recording_stop_started
        has_workers = any(worker.isRunning() for worker in self.workers) or self.transcription_worker.busy
        if not has_recording and not has_workers:
            self.transcription_worker.shutdown()
            if self.transcription_worker.wait(1000):
                event.accept()
            else:
                self._shutdown_requested = True
                event.ignore()
            return
        answer = QMessageBox.question(
            self,
            "Безопасное завершение",
            "Сначала завершить запись и дождаться текущих фоновых операций? "
            "Ожидающие транскрибации сохранятся и продолжатся при следующем запуске.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            event.ignore()
            return
        event.ignore()
        self._shutdown_requested = True
        self.transcription_worker.shutdown()
        self.timer.stop()
        self.latex_poll_timer.stop()
        self.content_maintenance_timer.stop()
        self.quick_countdown_timer.stop()
        self.start_button.setEnabled(False)
        self.quick_start_button.setEnabled(False)
        self._set_status("Завершаю текущие операции…", "working")
        if self.recorder and self.recorder.active and not self._recording_stop_started:
            self._stop_recording_async("Приложение закрывается; запись корректно завершается")
        self._maybe_finish_shutdown()

    def _maybe_finish_shutdown(self) -> None:
        if not self._shutdown_requested:
            return
        recording_busy = bool(self.recorder and self.recorder.active) or self._recording_stop_started
        workers_busy = any(worker.isRunning() for worker in self.workers)
        if recording_busy or workers_busy or self.transcription_worker.isRunning():
            return
        self._shutdown_ready = True
        QTimer.singleShot(0, self.close)

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
                f"потеряно блоков: {dropped}; задержка writer: {health.max_writer_latency_ms:.1f} мс; "
                f"тишина: {health.microphone_silence_seconds:.0f} / "
                f"{health.system_silence_seconds:.0f} с; переподключения: {health.reconnect_attempts}"
            )
            timeout = self.config.recording.device_timeout_seconds
            if health.stream_errors:
                self._stop_recording_async("Ошибка аудиоустройства: " + "; ".join(health.stream_errors))
                return
            if self.recording_seconds > timeout and (
                health.microphone_callback_age_seconds > timeout
                or health.system_callback_age_seconds > timeout
            ):
                self._stop_recording_async("Потерян поток аудиоустройства; сохранены доступные чанки записи")
                return
            silence_limit = self.config.recording.silence_warning_seconds
            warnings = []
            if health.microphone_silence_seconds >= silence_limit:
                warnings.append(f"микрофон молчит {health.microphone_silence_seconds:.0f} с")
            if health.system_silence_seconds >= silence_limit:
                warnings.append(f"звук ученика отсутствует {health.system_silence_seconds:.0f} с")
            if dropped:
                warnings.append(f"потеряно блоков: {dropped}")
            warning = "; ".join(warnings)
            if warning and warning != self._active_audio_warning:
                self._active_audio_warning = warning
                self._set_status("Проверьте аудио · " + warning, "warning")
                logging.warning("Контроль записи: %s", warning)
            elif not warning and self._active_audio_warning:
                self._active_audio_warning = ""
                self._set_status("Идёт запись", "working")


def main() -> None:
    force_setup = "--setup" in sys.argv
    if force_setup:
        sys.argv.remove("--setup")
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/app.yaml")
    config = AppConfig.load(config_path)
    configure_logging(config.workspace)
    install_exception_hook()
    app = QApplication(sys.argv)
    app.setApplicationName("Tutor Assistant")
    app.setOrganizationName("Tutor Assistant")
    apply_theme(app)
    if force_setup or not config.setup_completed:
        from .setup_wizard import SetupWizard

        wizard = SetupWizard(config, config_path)
        if wizard.exec() != QDialog.Accepted:
            raise SystemExit(0)
        config = AppConfig.load(config_path)
        configure_logging(config.workspace)
    window = MainWindow(config_path)
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
