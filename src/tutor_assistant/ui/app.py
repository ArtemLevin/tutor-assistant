from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

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
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..application import (
    AudioInputDeviceSnapshot,
    BackupMaintenanceCoordinator,
    BackupMaintenanceSnapshot,
    LatexMonitorCoordinator,
    LatexMonitorScanTrigger,
    NormalizationAfterWorkerAction,
    NormalizationAutoAction,
    NormalizationAutoContext,
    NormalizationCoordinator,
    NormalizationManualStartContext,
    NormalizationStartBlock,
    RecordingHealthAction,
    RecordingHealthMonitor,
    RecordingHealthPolicy,
    RecordingHealthSample,
    RecordingRuntimeRecorder,
    SystemAudioSourceSnapshot,
    TranscriptionAudioMissingError,
    TranscriptionPumpContext,
    TranscriptionQueueCoordinator,
)
from ..config import AppConfig, load_students
from ..content import ContentMaintenanceResult
from ..content_browser import is_audio_path
from ..crash import read_crash_marker
from ..crm import CrmStore
from ..domain import JobStatus, Lesson
from ..logging_config import (
    configure_logging,
    enable_native_fault_handler,
    install_exception_hook,
    install_qt_message_handler,
    log_directory,
)
from ..normalization import NormalizationService, SourceSegment
from ..normalization.models import (
    NormalizationExecution,
    NormalizationRunStatus,
    NormalizedTranscript,
)
from ..normalization.protocol import CancellationToken
from ..paths import default_config_path
from ..pipeline import LessonPipeline
from ..playback import PlaybackController, PlaybackSegment
from ..publisher import publication_payload_files
from ..quick_start import evaluate_readiness, selected_profile
from ..security.cloud_consent import (
    CloudConsentReceipt,
    CloudConsentScope,
    CloudConsentSession,
)
from ..security.credentials import (
    delete_yandex_api_key,
    save_yandex_api_key,
)
from ..transcript_editing import select_verified_text
from .accessibility import sync_text_status
from .crm import SchedulePage, StudentsPage
from .latex_monitor_presentation import (
    LatexMonitorPresentation,
    build_latex_monitor_failure_presentation,
    build_latex_monitor_no_update_presentation,
    build_latex_monitor_result_presentation,
    build_latex_monitor_scanning_presentation,
    build_latex_monitor_toggle_presentation,
)
from .localization import select_subject, set_subject_combo, subject_value
from .normalization import NormalizationReviewDialog
from .normalization_presentation import (
    NormalizationControlContext,
    build_normalization_controls,
    build_normalization_failure_presentation,
    build_normalization_ready_presentation,
)
from .normalization_provider import (
    provider_configuration_error,
    provider_hint,
    provider_label,
    provider_models,
    select_provider_config,
    with_provider_model,
)
from .normalization_worker import NormalizationWorker
from .parallel_review import ParallelReviewPolicy
from .playback import QtPlaybackBackend, QtStopScheduler
from .recording_presentation import (
    RecordingPanelPhase,
    RecordingTickPresentation,
    build_recording_tick_presentation,
    recording_panel_visual,
)
from .student_content import StudentContentPage
from .theme import apply_theme, refresh_style, set_button_kind, set_status
from .transcript_workspace import (
    NormalizationSettingsDialog,
    TranscriptWorkspace,
)
from .transcription_queue_presentation import build_transcription_queue_presentation
from .transcription_worker import TranscriptionWorker


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
            logging.exception("Фоновая операция завершилась необработанной ошибкой")
            self.failed.emit(traceback.format_exc())


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
        self.backup_coordinator = BackupMaintenanceCoordinator(
            self.content_service,
            self.config.workspace,
            enabled=self.config.content.backup_enabled,
            interval_hours=self.config.content.backup_interval_hours,
            retention_count=self.config.content.backup_retention_count,
        )
        self.normalization_service = NormalizationService(
            self.config.normalization,
            self.content_service,
        )
        recovered_normalizations = self.normalization_service.recover_interrupted()
        if recovered_normalizations:
            logging.info(
                "event=normalization_recovered count=%d",
                recovered_normalizations,
            )
        self.normalization_coordinator = NormalizationCoordinator()
        self.latex_monitor_coordinator = LatexMonitorCoordinator()
        self._normalization_cancellation: CancellationToken | None = None
        self._normalization_execution: NormalizationExecution | None = None
        self._normalization_lesson_id: str | None = None
        self._cloud_consent_session = CloudConsentSession()
        self.devices: list[AudioInputDeviceSnapshot] = []
        self.system_sources: list[SystemAudioSourceSnapshot] = []
        self.lesson: Lesson | None = None
        self.recording_lesson: Lesson | None = None
        self.recorder: RecordingRuntimeRecorder | None = None
        self.recording_health_monitor = RecordingHealthMonitor(
            RecordingHealthPolicy(
                device_timeout_seconds=self.config.recording.device_timeout_seconds,
                silence_warning_seconds=self.config.recording.silence_warning_seconds,
            )
        )
        self._recording_lease = None
        self.preflight_passed = False
        self.preflight_result = None
        self._recording_stop_started = False
        self._quick_start_pending = False
        self._quick_auto_transcribe_active = False
        self._quick_countdown_remaining = 0
        self._scheduled_occurrence_id: int | None = None
        self.recording_seconds = 0
        self.workers: list[Worker] = []
        self.transcription_queue_coordinator = TranscriptionQueueCoordinator(
            self.pipeline.store,
            retry_state_writer=self._persist_transcription_retry_state,
        )
        self._loading_segments = False
        self._summary_dirty = False
        self._shutdown_requested = False
        self._shutdown_ready = False
        self.transcription_worker = TranscriptionWorker(self.pipeline)
        self.transcription_worker.succeeded.connect(self._background_transcription_ready)
        self.transcription_worker.failed.connect(self._background_transcription_failed)
        self.transcription_worker.became_idle.connect(self._maybe_finish_shutdown)
        self.transcription_worker.became_idle.connect(self._pump_auto_normalization)
        self.transcription_worker.finished.connect(self._maybe_finish_shutdown)
        self.playback_backend = QtPlaybackBackend(self)
        self.playback_scheduler = QtStopScheduler(self)
        self.playback_controller = PlaybackController(
            self.playback_backend,
            self.playback_scheduler,
            lambda: self._parallel_policy().audio_playback_allowed,
            self._playback_error,
        )
        self.playback_backend.error_occurred.connect(self.playback_controller.report_backend_error)
        self.quick_countdown_timer = QTimer(self)
        self.quick_countdown_timer.setInterval(1000)
        self.quick_countdown_timer.timeout.connect(self._quick_countdown_tick)
        self.latex_poll_timer = QTimer(self)
        self.latex_poll_timer.setInterval(self.config.latex.poll_seconds * 1000)
        self.latex_poll_timer.timeout.connect(
            lambda: self.scan_remote_latex(periodic=True)
        )
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
        self.backup_maintenance_timer = QTimer(self)
        self.backup_maintenance_timer.setInterval(
            min(self.config.content.backup_interval_hours * 3_600_000, 300_000)
        )
        self.backup_maintenance_timer.timeout.connect(self._run_scheduled_backup)
        if self.config.content.backup_enabled:
            self.backup_maintenance_timer.start()
            QTimer.singleShot(750, self._run_scheduled_backup)
        if self.config.content.maintenance_enabled:
            self.content_maintenance_timer.start()
            QTimer.singleShot(1000, self._run_content_maintenance)
        QTimer.singleShot(0, self._offer_recovery)
        QTimer.singleShot(100, self._restore_background_jobs)
        QTimer.singleShot(150, self._offer_unfinished_job)
        QTimer.singleShot(300, self._offer_previous_crash_support)
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
        self.header_subtitle = QLabel("Запись занятия, проверка транскрипта и выпуск материалов в одном окне")
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
        self.materials_tab_index = self.tabs.addTab(self.student_content_page, "08  Материалы")
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
        sync_text_status(self.app_status, "Состояние приложения")
        self.statusBar().setAccessibleName("Строка состояния приложения")
        self.statusBar().setAccessibleDescription(message)
        self.statusBar().showMessage(message)
        if hasattr(self, "header_title"):
            self.header_title.setToolTip(message)

    def _go_to(self, index: int) -> None:
        self._set_mode("detailed")
        self.tabs.setCurrentIndex(index)

    def _set_mode(self, mode: str) -> None:
        quick = mode == "quick"
        target = (
            self.quick_page
            if quick
            else getattr(self, "navigation_shell", self.tabs)
        )
        target_index = self.content_stack.indexOf(target)
        if target_index >= 0:
            self.content_stack.setCurrentWidget(target)
        else:
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

    def _run_scheduled_backup(self) -> None:
        recording_busy = bool(self.recorder and self.recorder.active) or self._recording_stop_started
        decision = self.backup_coordinator.decide(
            recording_active=recording_busy,
            shutdown_requested=self._shutdown_requested,
        )
        if decision.action.value != "run":
            return
        if any(getattr(worker, "purpose", "") == "scheduled-backup" for worker in self.workers):
            return
        worker = Worker(
            lambda: self.backup_coordinator.run_due(
                recording_active=recording_busy,
                shutdown_requested=self._shutdown_requested,
            )
        )
        worker.purpose = "scheduled-backup"
        worker.succeeded.connect(self._scheduled_backup_ready)
        worker.failed.connect(self._scheduled_backup_failed)
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _scheduled_backup_ready(self, result: object) -> None:
        if not isinstance(result, BackupMaintenanceSnapshot):
            self._scheduled_backup_failed("Получен некорректный статус резервной копии")
            return
        if result.last_error:
            self._scheduled_backup_failed(result.last_error)
            return
        logging.info(
            "Автоматическая резервная копия проверена; scheduled=%s next=%s",
            result.scheduled_copy_count,
            result.next_due_at,
        )

    def _scheduled_backup_failed(self, details: str) -> None:
        logging.error("Автоматическое резервирование недоступно: %s", details)
        self._set_status("Не удалось проверить резервную копию", "warning")

    def _offer_previous_crash_support(self) -> None:
        marker = read_crash_marker(self.config.workspace)
        if marker is None:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Предыдущий запуск завершился аварийно")
        dialog.setText("Предыдущий запуск завершился аварийно. Диагностика сохраняется только локально.")
        support = dialog.addButton("Создать пакет диагностики", QMessageBox.ActionRole)
        logs = dialog.addButton("Открыть журнал", QMessageBox.ActionRole)
        dialog.addButton("Продолжить", QMessageBox.AcceptRole)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.buttonClicked.connect(
            lambda button: self._create_support_bundle()
            if button is support
            else self._open_logs()
            if button is logs
            else None
        )
        dialog.open()

    def _run_content_maintenance(self) -> None:
        if (
            not self.config.content.maintenance_enabled
            or self._shutdown_requested
            or (self.recorder and self.recorder.active)
            or any(getattr(worker, "purpose", "") == "content-maintenance" for worker in self.workers)
        ):
            return

        def maintain() -> ContentMaintenanceResult:
            return self.content_service.run_maintenance(
                auto_repair=self.config.content.auto_repair,
                purge_expired=self.config.content.auto_purge_trash,
                cleanup_temporary=self.config.content.auto_cleanup_temporary,
                temporary_retention=timedelta(hours=self.config.content.temporary_retention_hours),
                backup_enabled=False,
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
            self.transcription_queue_coordinator.discard(lesson_id)
        except ValueError:
            logging.warning("Активное задание не удалено из очереди: %s", lesson_id)
        if self.lesson and self.lesson.lesson_id == lesson_id:
            self.lesson = None
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
        select_subject(self.quick_subject, subject)
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
        """Startup hook overridden by the production recording-recovery adapter."""

        return

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

    def _persist_transcription_retry_state(self, lesson: Lesson) -> None:
        self.pipeline.save_state(
            lesson,
            "status",
            "error",
            force_status=True,
        )

    def _restore_background_jobs(self) -> None:
        restored = self.transcription_queue_coordinator.restore_history(
            self.pipeline.store.list(limit=1000),
            self.pipeline.store.list_transcription_jobs(),
        )
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
        index = self.subject.findData(lesson.subject)
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
        self._sync_normalization_controls()

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
        set_subject_combo(
            self.quick_subject,
            selected=self.config.quick_start.last_subject or profile.subject,
        )
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

        self.quick_readiness_button = QPushButton("Проверить")
        self.quick_readiness_button.setObjectName("quickStatusButton")
        self.quick_readiness_button.setAccessibleName("Открыть подробную проверку готовности")
        self.quick_readiness_button.clicked.connect(self._show_readiness_dialog)
        top_row.addWidget(self.quick_readiness_button)

        self.quick_options_button = QPushButton("···")
        self.quick_options_button.setObjectName("quickIconButton")
        self.quick_options_button.setToolTip("Изменить профиль и предмет")
        self.quick_options_button.clicked.connect(self._show_quick_options_dialog)
        top_row.addWidget(self.quick_options_button)
        surface_layout.addLayout(top_row)

        quick_context = QFrame()
        quick_context.setObjectName("infoPanel")
        quick_context_layout = QHBoxLayout(quick_context)
        quick_context_layout.setContentsMargins(12, 9, 12, 9)
        quick_context_layout.setSpacing(10)
        self.quick_profile_text = QLabel()
        self.quick_profile_text.setObjectName("muted")
        self.quick_subject_text = QLabel()
        self.quick_subject_text.setObjectName("muted")
        quick_context_layout.addWidget(self.quick_profile_text)
        quick_context_layout.addWidget(self.quick_subject_text)
        quick_context_layout.addStretch(1)
        surface_layout.addWidget(quick_context)

        self.quick_readiness_text = QLabel()
        self.quick_readiness_text.setObjectName("readinessSummary")
        self.quick_readiness_text.setWordWrap(True)
        self.quick_readiness_text.setAccessibleName("Состояние готовности быстрого урока")
        surface_layout.addWidget(self.quick_readiness_text)
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
        select_subject(self.quick_subject, profile.subject)
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
        profile = selected_profile(self.config, self.quick_profile.currentData())
        self.quick_profile_text.setText(f"Профиль: {profile.name}")
        self.quick_subject_text.setText(f"Предмет: {self.quick_subject.currentText()}")
        self.quick_readiness_button.setText("Проверить")
        self.quick_readiness_button.setProperty("tone", "ready" if readiness.ready else "blocked")
        if readiness.ready:
            readiness_text = "Готово к старту · данные урока и аудио проверены"
        else:
            blocker = readiness.blockers[0].detail if readiness.blockers else "Проверьте параметры урока"
            readiness_text = f"Требуется действие · {blocker}"
        self.quick_readiness_text.setText(readiness_text)
        self.quick_readiness_text.setProperty("tone", "ready" if readiness.ready else "blocked")
        sync_text_status(self.quick_readiness_text, "Готовность быстрого урока")
        self.quick_readiness_button.setAccessibleDescription(readiness_text)
        lines = [f"{'✓' if item.ready else '!'} {item.label}: {item.detail}" for item in readiness.items]
        lines.append("")
        lines.append("Нажмите, чтобы открыть подробную проверку")
        self.quick_readiness_button.setToolTip("\n".join(lines))
        refresh_style(self.quick_readiness_button)
        refresh_style(self.quick_readiness_text)
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
        selected_subject = subject_value(
            self.quick_subject.currentData() or self.quick_subject.currentText()
        )
        select_subject(self.subject, selected_subject)
        self.topic.setText(self.quick_topic.text().strip())
        self.lesson_date.setDate(QDate.currentDate())
        self.config.quick_start.default_profile_id = str(self.quick_profile.currentData())
        self.config.quick_start.last_student_id = self.quick_student.currentData()
        self.config.quick_start.last_subject = selected_subject
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
        set_subject_combo(self.subject)
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
        ready_visual = recording_panel_visual(RecordingPanelPhase.READY)
        self.recording_state_label = QLabel(ready_visual.text)
        self.recording_state_label.setObjectName("recordingState")
        self.recording_state_label.setProperty("active", ready_visual.active)
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
            if source is None:
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
        if source is not None:
            self.config.recording.system_device_id = source.device_id
            self.config.recording.system_backend = source.backend
            self.config.recording.loopback_device = source.legacy_index
        self.preflight_passed = False
        self.preflight_result = None
        self.config.save(self.config_path)
        self._refresh_quick_readiness()

    def _transcript_tab(self) -> QWidget:
        page = QWidget()
        workspace = TranscriptWorkspace(page)
        self.transcript_workspace = workspace

        self.segment_table = workspace.segment_table
        self.play_segment_button = workspace.play_segment_button
        self.playback_speed = workspace.playback_speed
        self.transcript = workspace.transcript_editor
        self.approve = workspace.approve_button

        self.segment_table.doubleClicked.connect(self.play_selected_segment)
        self.segment_table.itemChanged.connect(lambda _item: self._schedule_draft_save())
        self.play_segment_button.clicked.connect(self.play_selected_segment)
        self.transcript.textChanged.connect(self._summary_changed)
        self.approve.setShortcut(QKeySequence("Ctrl+Return"))
        self.approve.setToolTip("Подтвердить транскрипт и перейти к публикации · Ctrl+Enter")
        self.approve.setEnabled(False)
        self.approve.clicked.connect(self.approve_transcript)

        workspace.settings_button.clicked.connect(self._show_normalization_settings)
        workspace.primary_action_button.clicked.connect(
            self._handle_transcript_primary_action
        )
        workspace.review_result_button.clicked.connect(self.open_normalization_result)
        workspace.restart_action.triggered.connect(
            lambda: self.normalize_current_transcript(force=True)
        )
        workspace.open_artifact_action.triggered.connect(
            self._open_normalization_artifact
        )
        workspace.show_warnings_action.triggered.connect(
            self.open_normalization_result
        )
        workspace.reject_action.triggered.connect(self.reject_normalization_result)

        compatibility = QWidget(page)
        compatibility.hide()
        self._normalization_compatibility_controls = compatibility

        self.normalization_provider = QComboBox(compatibility)
        for provider in ("ollama", "yandex_ai_studio"):
            self.normalization_provider.addItem(provider_label(provider), provider)
        self.normalization_provider.setCurrentIndex(
            self.normalization_provider.findData(self.config.normalization.provider)
        )
        self.normalization_provider.currentIndexChanged.connect(
            self._normalization_provider_changed
        )

        self.normalization_model = QComboBox(compatibility)
        self.normalization_model.setEditable(True)

        self.normalization_retry_requests = QSpinBox(compatibility)
        self.normalization_retry_requests.setRange(0, 3)
        self.normalization_retry_requests.setValue(
            self.config.normalization.retry_requests
        )
        self.normalization_retry_requests.valueChanged.connect(
            self._normalization_retry_requests_changed
        )

        self.normalization_provider_hint = QLabel(compatibility)
        self.save_yandex_key_button = QPushButton(compatibility)
        self.delete_yandex_key_button = QPushButton(compatibility)

        self.normalize_button = workspace.primary_action_button
        self.cancel_normalization_button = workspace.primary_action_button
        self.retry_normalization_button = workspace.primary_action_button
        self.open_normalization_button = workspace.primary_action_button
        self.apply_normalization_button = workspace.primary_action_button
        self.reject_normalization_button = workspace.overflow_button
        self.normalization_progress_label = workspace.process_detail
        self.normalization_progress = workspace.progress

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(workspace)
        self._transcript_primary_action = "start"
        self._sync_normalization_provider_ui()
        self._sync_normalization_controls()
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
        self.processing_list.itemSelectionChanged.connect(self._sync_processing_actions)
        self.processing_list.itemDoubleClicked.connect(self._open_processing_item)
        layout.addWidget(self.processing_list, 1)
        processing_actions = QHBoxLayout()
        hint = QLabel("Выберите задание, затем откройте готовый транскрипт или повторите ошибку")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        processing_actions.addWidget(hint, 1)
        self.processing_open_button = set_button_kind(
            QPushButton("Открыть выбранное"),
            "primary",
        )
        self.processing_open_button.setEnabled(False)
        self.processing_open_button.clicked.connect(self._open_selected_processing_item)
        processing_actions.addWidget(self.processing_open_button)
        layout.addLayout(processing_actions)
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
        preview_actions = QHBoxLayout()
        preview_hint = QLabel("Выберите страницу для открытия в системном просмотрщике")
        preview_hint.setObjectName("muted")
        preview_hint.setWordWrap(True)
        preview_actions.addWidget(preview_hint, 1)
        self.open_pdf_preview_button = set_button_kind(
            QPushButton("Открыть страницу"),
            "ghost",
        )
        self.open_pdf_preview_button.setEnabled(False)
        self.open_pdf_preview_button.clicked.connect(self._open_selected_pdf_preview)
        preview_actions.addWidget(self.open_pdf_preview_button)
        preview_layout.addLayout(preview_actions)
        self.pdf_previews = QListWidget()
        self.pdf_previews.itemSelectionChanged.connect(self._sync_pdf_preview_action)
        self.pdf_previews.itemDoubleClicked.connect(
            lambda _item: self._open_selected_pdf_preview()
        )
        preview_layout.addWidget(self.pdf_previews)
        results.addWidget(preview_box, 2)
        layout.addLayout(results, 1)
        return page

    def _sync_pdf_preview_action(self) -> None:
        if hasattr(self, "open_pdf_preview_button"):
            self.open_pdf_preview_button.setEnabled(self.pdf_previews.currentItem() is not None)

    def _open_selected_pdf_preview(self) -> None:
        item = self.pdf_previews.currentItem()
        if item is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(item.data(256))))

    def _build_lesson_from_form(self) -> Lesson:
        """Build a Lesson draft from the shared form without persisting it."""

        if not self.topic.text().strip():
            raise ValueError("Укажите тему занятия")
        selected = next(item for item in self.students if item.id == self.student.currentData())
        value = self.lesson_date.date()
        return Lesson(
            student=selected,
            subject=subject_value(self.subject.currentData() or self.subject.currentText()),
            topic=self.topic.text().strip(),
            lesson_date=date(value.year(), value.month(), value.day()),
        )

    def _create_lesson_from_form(self) -> Lesson:
        """Persist a form-backed Lesson for non-recording workflows such as import."""

        lesson = self._build_lesson_from_form()
        self.pipeline.create(lesson)
        return lesson

    def start_recording(self) -> None:
        """Command port implemented by the production recording-start adapter."""

        raise NotImplementedError(
            "Recording start is owned by the production application adapter"
        )

    def stop_recording(self) -> None:
        self._stop_recording_async()

    def _stop_recording_async(self, reason: str | None = None) -> None:
        """Command port implemented by the production stop/finalize adapter."""

        del reason
        raise NotImplementedError(
            "Recording stop/finalization is owned by the production application adapter"
        )

    def choose_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Аудиозапись", "", "Audio (*.wav *.mp3 *.m4a *.flac)")
        if path:
            self.audio_path.setText(path)
            self._set_status("Аудиофайл выбран")

    def test_devices(self) -> None:
        self._begin_preflight(show_intro=True)

    def _begin_preflight(self, show_intro: bool) -> None:
        del show_intro
        raise NotImplementedError(
            "Audio preflight is owned by the production audio application adapter"
        )

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
                self.lesson = self._create_lesson_from_form()
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
            self._set_status(
                f"{lesson.student.full_name}: добавлено в фоновую очередь",
                "working",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", str(exc))

    def _enqueue_transcription(self, lesson: Lesson, audio: Path) -> None:
        job = self.transcription_queue_coordinator.enqueue(lesson, audio)
        logging.info("Транскрибация поставлена в очередь: lesson=%s audio=%s", job.id, audio)
        self._update_transcription_queue_ui()
        self._pump_transcription_queue()

    def _pump_transcription_queue(self) -> None:
        submission = self.transcription_queue_coordinator.pump(
            TranscriptionPumpContext(
                shutdown_requested=self._shutdown_requested,
                normalization_active=self._normalization_cancellation is not None,
            )
        )
        if submission is None:
            return
        self._update_transcription_queue_ui()
        self.transcription_worker.submit(
            submission.job_id,
            submission.lesson,
            submission.audio,
        )

    def _background_transcription_ready(self, job_id: str, lesson: Lesson) -> None:
        self.transcription_queue_coordinator.complete(job_id, lesson)
        if self.config.normalization.enabled and self.config.normalization.auto_run:
            self.normalization_coordinator.enqueue_auto(lesson.lesson_id)
        self._update_transcription_queue_ui()
        self._set_status(f"Транскрипт готов · {lesson.student.full_name}", "warning")
        logging.info("Фоновая транскрибация завершена: lesson=%s", lesson.lesson_id)
        self._pump_transcription_queue()
        QTimer.singleShot(0, self._pump_auto_normalization)

    def _background_transcription_failed(self, job_id: str, details: str) -> None:
        job = self.transcription_queue_coordinator.fail(job_id, details)
        self._update_transcription_queue_ui()
        logging.error("Фоновая транскрибация завершилась с ошибкой: lesson=%s\n%s", job_id, details)
        self._set_status(f"Ошибка транскрибации · {job.lesson.student.full_name}", "error")
        self._pump_transcription_queue()

    def _update_transcription_queue_ui(self) -> None:
        if not hasattr(self, "processing_list"):
            return
        presentation = build_transcription_queue_presentation(
            self.transcription_queue_coordinator.snapshot()
        )
        self.processing_list.clear()
        for row in presentation.rows:
            item = QListWidgetItem(row.text)
            item.setData(256, row.job_id)
            if row.tooltip:
                item.setToolTip(row.tooltip)
            self.processing_list.addItem(item)
        self.processing_summary.setText(presentation.summary_text)
        self.quick_queue_button.setText(presentation.badge_text)
        self.quick_queue_button.setToolTip(presentation.badge_tooltip)

    def _show_processing_queue(self) -> None:
        self._set_mode("detailed")
        self.tabs.setCurrentIndex(4)

    def _sync_processing_actions(self) -> None:
        if hasattr(self, "processing_open_button"):
            self.processing_open_button.setEnabled(self.processing_list.currentItem() is not None)

    def _open_selected_processing_item(self) -> None:
        item = self.processing_list.currentItem()
        if item is not None:
            self._open_processing_item(item)

    def _retry_transcription_job(self, job_id: str) -> bool:
        try:
            self.transcription_queue_coordinator.retry(job_id)
        except TranscriptionAudioMissingError as exc:
            QMessageBox.critical(self, "Ошибка", f"Аудиофайл не найден: {exc.path}")
            return False
        self._update_transcription_queue_ui()
        self._pump_transcription_queue()
        return True

    def _open_processing_item(self, item: QListWidgetItem) -> None:
        job = self.transcription_queue_coordinator.get(str(item.data(256)))
        if job is None:
            return
        if (self.recorder and self.recorder.active) or self._recording_stop_started:
            QMessageBox.warning(
                self,
                "Идёт запись",
                "Завершите текущую запись перед открытием другого занятия.",
            )
            return
        if job.status.value == "failed":
            answer = QMessageBox.question(
                self,
                "Ошибка транскрибации",
                f"{job.error or 'Неизвестная ошибка'}\n\nПовторить транскрибацию?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                self._retry_transcription_job(job.id)
            return
        if job.status.value != "ready":
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
        if hasattr(self, "transcript_workspace"):
            self.transcript_workspace.set_segment_count(len(segments))

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

    def _current_source_segments(self) -> list[SourceSegment]:
        segments: list[SourceSegment] = []
        for row in range(self.segment_table.rowCount()):
            start_item = self.segment_table.item(row, 0)
            end_item = self.segment_table.item(row, 1)
            speaker_item = self.segment_table.item(row, 2)
            text_item = self.segment_table.item(row, 3)
            if text_item is None:
                continue
            speaker = speaker_item.text().strip() if speaker_item else ""
            segments.append(
                SourceSegment(
                    source_segment_id=row + 1,
                    start=start_item.data(256) if start_item else None,
                    end=end_item.data(256) if end_item else None,
                    speaker=None if speaker in {"", "—"} else speaker,
                    text=text_item.text(),
                )
            )
        return segments

    def _show_normalization_settings(self) -> None:
        if self.normalization_coordinator.active:
            QMessageBox.warning(
                self,
                "LLM-фильтрация",
                "Нельзя менять настройки во время выполняющейся фильтрации.",
            )
            return
        dialog = NormalizationSettingsDialog(
            provider=self.config.normalization.provider,
            model=self.config.normalization.effective_model,
            retry_requests=self.config.normalization.retry_requests,
            parent=self,
        )
        dialog.save_key_button.clicked.connect(self._configure_yandex_key)
        dialog.delete_key_button.clicked.connect(self._delete_yandex_key)
        if dialog.exec() != QDialog.Accepted:
            return

        selected_provider = dialog.selected_provider
        provider_index = self.normalization_provider.findData(selected_provider)
        self.normalization_provider.setCurrentIndex(provider_index)
        if self._selected_normalization_provider() != selected_provider:
            return

        self.normalization_model.setCurrentText(dialog.selected_model)
        self.normalization_retry_requests.setValue(dialog.retry_requests)
        try:
            self._persist_selected_normalization_model()
        except Exception as exc:
            QMessageBox.warning(self, "LLM-фильтрация", str(exc))
            return
        self._sync_normalization_provider_ui()
        self._sync_normalization_controls()
        self._set_status("Настройки LLM-фильтрации сохранены")

    def _normalization_settings_summary(self) -> str:
        retries = self.config.normalization.retry_requests
        retry_label = {
            0: "без повторов",
            1: "1 повтор",
            2: "2 повтора",
            3: "3 повтора",
        }[retries]
        return (
            f"{provider_label(self.config.normalization.provider)} · "
            f"{self.config.normalization.effective_model} · {retry_label}"
        )

    def _handle_transcript_primary_action(self) -> None:
        action = getattr(self, "_transcript_primary_action", "start")
        if action == "settings":
            self._show_normalization_settings()
        elif action == "cancel":
            self.cancel_normalization()
        elif action == "review":
            self.open_normalization_result()
        elif action == "retry":
            self.normalize_current_transcript(force=True)
        else:
            self.normalize_current_transcript()

    def _open_normalization_artifact(self) -> None:
        if not self.lesson:
            return
        run = self.normalization_service.runs.latest(self.lesson.lesson_id)
        if not run or not run.artifact_path:
            QMessageBox.warning(
                self,
                "LLM-фильтрация",
                "Текстовый файл результата не найден",
            )
            return
        artifact = self.content_service.workspace / run.artifact_path
        if not artifact.is_file():
            QMessageBox.warning(
                self,
                "LLM-фильтрация",
                f"Файл результата отсутствует: {artifact}",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(artifact.resolve())))

    def _sync_transcript_workspace_context(self) -> None:
        if not hasattr(self, "transcript_workspace"):
            return
        if not self.lesson:
            self.transcript_workspace.set_context(
                "Откройте занятие из очереди обработки или архива материалов.",
                "Нет занятия",
                "neutral",
            )
            return
        status_labels = {
            JobStatus.REVIEW_REQUIRED: ("Требует проверки", "warning"),
            JobStatus.READY: ("Подтверждён", "success"),
            JobStatus.PUBLISHED: ("Опубликован", "success"),
            JobStatus.FAILED: ("Ошибка", "error"),
        }
        status, tone = status_labels.get(
            self.lesson.status,
            (self.lesson.status.value, "neutral"),
        )
        detail = (
            f"{self.lesson.student.full_name} · {self.lesson.subject} · "
            f"{self.lesson.lesson_date:%d.%m.%Y} · {self.lesson.topic}"
        )
        self.transcript_workspace.set_context(detail, status, tone)
        self.transcript_workspace.set_segment_count(self.segment_table.rowCount())

    def _normalization_preview(self, run):
        if (
            run is None
            or run.status not in {
                NormalizationRunStatus.REVIEW_REQUIRED,
                NormalizationRunStatus.APPROVED,
            }
            or not run.artifact_path
        ):
            self.transcript_workspace.clear_result()
            return None
        artifact = self.content_service.workspace / run.artifact_path
        if not artifact.is_file():
            self.transcript_workspace.clear_result()
            return None
        try:
            transcript = self.normalization_service.load_result(run)
        except Exception:
            logging.exception("Не удалось загрузить preview результата LLM-фильтрации")
            self.transcript_workspace.clear_result()
            return None
        statistics = transcript.statistics
        summary = (
            f"Сохранено {statistics.retained_ratio * 100:.1f}% текста · "
            f"кандидатов на проверку: {statistics.review_candidate_chunks} · "
            f"fallback-блоков: {statistics.source_fallback_chunks} · "
            f"запросов к модели: {statistics.provider_requests}"
        )
        self.transcript_workspace.set_result_preview(
            transcript.educational_text,
            summary=summary,
            warnings=transcript.quality.warnings,
        )
        return transcript

    def _selected_normalization_provider(self) -> str:
        if not hasattr(self, "normalization_provider"):
            return self.config.normalization.provider
        return str(self.normalization_provider.currentData() or "ollama")

    def _set_normalization_provider_combo(self, provider: str) -> None:
        if not hasattr(self, "normalization_provider"):
            return
        self.normalization_provider.blockSignals(True)
        self.normalization_provider.setCurrentIndex(self.normalization_provider.findData(provider))
        self.normalization_provider.blockSignals(False)

    def _replace_normalization_config(self, config) -> None:
        self.config.normalization = config
        self.config.save(self.config_path)
        self.normalization_service = NormalizationService(
            config,
            self.content_service,
        )

    def _normalization_retry_requests_changed(self, value: int) -> None:
        updated = self.config.normalization.model_copy(
            update={"retry_requests": value}
        )
        self._replace_normalization_config(updated)
        self._set_status(f"Повторных запросов LLM при ошибке: {value}")

    def _sync_normalization_provider_ui(self) -> None:
        if not hasattr(self, "normalization_provider"):
            return
        provider = self._selected_normalization_provider()
        configured_model = (
            self.config.normalization.yandex_model
            if provider == "yandex_ai_studio"
            else self.config.normalization.model
        )
        models = list(provider_models(provider))
        if configured_model and configured_model not in models:
            models.insert(0, configured_model)
        self.normalization_model.blockSignals(True)
        self.normalization_model.clear()
        self.normalization_model.addItems(models)
        self.normalization_model.setCurrentText(configured_model)
        self.normalization_model.blockSignals(False)
        self.normalization_provider_hint.setText(provider_hint(self.config.normalization))
        error = provider_configuration_error(self.config.normalization)
        tooltip = error or provider_hint(self.config.normalization)
        self.normalization_provider.setToolTip(tooltip)
        self.normalization_model.setToolTip(tooltip)
        cloud_selected = provider == "yandex_ai_studio"
        self.save_yandex_key_button.setVisible(cloud_selected)
        self.delete_yandex_key_button.setVisible(cloud_selected)

    def _normalization_provider_changed(self, _index: int) -> None:
        selected = self._selected_normalization_provider()
        current = self.config.normalization.provider
        if selected == current:
            self._sync_normalization_provider_ui()
            return
        if self.normalization_coordinator.active:
            self._set_normalization_provider_combo(current)
            QMessageBox.warning(
                self,
                "LLM-фильтрация",
                "Нельзя менять провайдера во время выполняющейся фильтрации.",
            )
            return

        folder_id = self.config.normalization.yandex_folder_id
        allow_cloud_processing = self.config.normalization.allow_cloud_processing
        if selected == "yandex_ai_studio":
            if not allow_cloud_processing:
                answer = QMessageBox.question(
                    self,
                    "Облачная обработка транскрипта",
                    "Текст занятия будет передан в Yandex AI Studio. "
                    "Аудиозапись не отправляется. Разрешить облачную обработку?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self._set_normalization_provider_combo(current)
                    return
                allow_cloud_processing = True
            if not (folder_id or "").strip():
                folder_id, accepted = QInputDialog.getText(
                    self,
                    "Yandex AI Studio",
                    "Yandex Cloud folder ID:",
                    text="",
                )
                if not accepted or not folder_id.strip():
                    self._set_normalization_provider_combo(current)
                    return

        try:
            updated = select_provider_config(
                self.config.normalization,
                selected,
                folder_id=folder_id,
                allow_cloud_processing=allow_cloud_processing,
            )
            self._replace_normalization_config(updated)
        except Exception as exc:
            self._set_normalization_provider_combo(current)
            QMessageBox.warning(self, "LLM-фильтрация", str(exc))
            return

        self._sync_normalization_provider_ui()
        self._sync_normalization_controls()
        error = provider_configuration_error(updated)
        if error:
            QMessageBox.information(
                self,
                "Настройка Yandex AI Studio",
                error + "\n\nЗадайте API-ключ в переменной окружения и перезапустите приложение.",
            )
        else:
            self._set_status(f"Провайдер LLM-фильтрации: {provider_label(selected)}")

    def _configure_yandex_key(self) -> None:
        value, accepted = QInputDialog.getText(
            self,
            "Yandex AI Studio",
            "API-ключ будет сохранён в Windows Credential Manager:",
            QLineEdit.Password,
        )
        if not accepted:
            return
        try:
            save_yandex_api_key(self.config.normalization, value)
            updated = self.config.normalization.model_copy(
                update={"credential_source": "system_store"}
            )
            self._replace_normalization_config(updated)
            self._sync_normalization_provider_ui()
            self._sync_normalization_controls()
            self._set_status("API-ключ Yandex AI Studio сохранён безопасно", "success")
        except Exception as exc:
            QMessageBox.warning(self, "Yandex AI Studio", str(exc))

    def _delete_yandex_key(self) -> None:
        answer = QMessageBox.question(
            self,
            "Yandex AI Studio",
            "Удалить API-ключ из Windows Credential Manager?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            delete_yandex_api_key(self.config.normalization)
            self._sync_normalization_provider_ui()
            self._sync_normalization_controls()
            self._set_status("Сохранённый API-ключ удалён", "warning")
        except Exception as exc:
            QMessageBox.warning(self, "Yandex AI Studio", str(exc))

    def _request_cloud_consent(
        self,
        lesson_id: str,
        model: str,
        segments: list[SourceSegment],
    ) -> CloudConsentReceipt | None:
        request = self.normalization_service.cloud_processing_request(
            lesson_id,
            model=model,
            source_segments=segments,
        )
        if self.config.normalization.effective_cloud_policy == "allow_for_session":
            existing = self._cloud_consent_session.find(request)
            if existing:
                return existing
        box = QMessageBox(self)
        box.setWindowTitle("Передача транскрипта в Yandex AI Studio")
        box.setIcon(QMessageBox.Warning)
        box.setText(
            "Будет отправлен текст реплик занятия.\n\n"
            f"Предметный профиль: {request.subject_profile}\n"
            f"Модель: {request.model}\n"
            f"Сегментов: {request.segment_count}\n"
            f"Символов: {request.character_count}\n"
            f"Блоков: {request.chunk_count}\n\n"
            "Аудио, ФИО ученика, lesson ID, локальные пути и таймкоды не отправляются."
        )
        once_button = box.addButton("Разрешить один запуск", QMessageBox.AcceptRole)
        session_button = box.addButton(
            "Разрешить до закрытия приложения",
            QMessageBox.YesRole,
        )
        box.addButton("Отмена", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is once_button:
            return self._cloud_consent_session.grant(request, CloudConsentScope.ONCE)
        if clicked is session_button:
            updated = self.config.normalization.model_copy(
                update={"cloud_policy": "allow_for_session"}
            )
            self._replace_normalization_config(updated)
            return self._cloud_consent_session.grant(request, CloudConsentScope.SESSION)
        return None

    def _persist_selected_normalization_model(self) -> str:
        provider = self._selected_normalization_provider()
        model = self.normalization_model.currentText().strip()
        updated = with_provider_model(self.config.normalization, provider, model)
        if updated != self.config.normalization:
            self._replace_normalization_config(updated)
            self._sync_normalization_provider_ui()
        return updated.effective_model

    def _sync_normalization_controls(self) -> None:
        if not hasattr(self, "transcript_workspace"):
            return
        self._sync_transcript_workspace_context()
        self.transcript_workspace.set_config_summary(
            self._normalization_settings_summary()
        )

        run = (
            self.normalization_service.runs.latest(self.lesson.lesson_id)
            if self.lesson
            else None
        )
        provider_error = provider_configuration_error(self.config.normalization)
        artifact_ready = bool(
            run
            and run.artifact_path
            and (self.content_service.workspace / run.artifact_path).is_file()
        )
        preview = self._normalization_preview(run)
        presentation = build_normalization_controls(
            NormalizationControlContext(
                lifecycle_state=self.normalization_coordinator.state,
                has_lesson=self.lesson is not None,
                enabled=self.config.normalization.enabled,
                has_segments=bool(self.segment_table.rowCount()),
                provider_error=provider_error,
                run_status=run.status if run else None,
                artifact_ready=artifact_ready,
                review_candidate_chunks=(
                    preview.statistics.review_candidate_chunks if preview else 0
                ),
                fallback_chunks=(
                    preview.statistics.source_fallback_chunks if preview else 0
                ),
                warning_count=len(preview.quality.warnings) if preview else 0,
                progress=self.normalization_coordinator.progress,
            )
        )

        self.normalization_provider.setEnabled(presentation.provider_enabled)
        self.transcript_workspace.settings_button.setEnabled(
            presentation.settings_enabled
        )
        self.transcript_workspace.set_review_action(
            visible=presentation.review_visible,
            enabled=presentation.review_enabled,
            text=presentation.review_text,
        )
        self.transcript_workspace.set_menu_state(
            restart=presentation.menu.restart,
            open_artifact=presentation.menu.open_artifact,
            show_warnings=presentation.menu.show_warnings,
            reject=presentation.menu.reject,
        )
        self._transcript_primary_action = presentation.primary.action
        primary_kwargs = {
            "enabled": presentation.primary.enabled,
            "visible": presentation.primary.visible,
        }
        if presentation.primary.kind is not None:
            primary_kwargs["kind"] = presentation.primary.kind
        self.transcript_workspace.set_primary_action(
            presentation.primary.text,
            **primary_kwargs,
        )
        if presentation.process.progress_total is not None:
            self.transcript_workspace.set_progress(
                total=presentation.process.progress_total,
                completed=presentation.process.progress_completed or 0,
                title=presentation.process.title,
                detail=presentation.process.detail,
            )
        else:
            self.transcript_workspace.set_process_state(
                presentation.process.title,
                presentation.process.detail,
                tone=presentation.process.tone,
                show_progress=presentation.process.show_progress,
            )

    def normalize_current_transcript(
        self,
        _checked: bool = False,
        *,
        force: bool = False,
        retry_indeterminate: bool = False,
    ) -> None:
        del _checked
        provider = self._selected_normalization_provider()
        decision = self.normalization_coordinator.evaluate_manual_start(
            NormalizationManualStartContext(
                lesson_id=self.lesson.lesson_id if self.lesson else None,
                provider=provider,
                provider_error=provider_configuration_error(self.config.normalization),
                has_segments=bool(self.segment_table.rowCount()),
                transcription_busy=(
                    self.transcription_worker.busy
                    or self.transcription_queue_coordinator.active is not None
                ),
            )
        )
        if not decision.allowed:
            if decision.block == NormalizationStartBlock.NO_LESSON:
                QMessageBox.warning(
                    self,
                    "LLM-фильтрация",
                    "Сначала откройте транскрипт занятия",
                )
            elif decision.block == NormalizationStartBlock.PROVIDER_ERROR:
                QMessageBox.warning(
                    self,
                    "LLM-фильтрация",
                    decision.detail or "Провайдер LLM не настроен",
                )
            elif decision.block == NormalizationStartBlock.TRANSCRIPTION_BUSY:
                QMessageBox.warning(
                    self,
                    "LLM-фильтрация",
                    "Дождитесь завершения активной Whisper-транскрибации: оба процесса используют CPU.",
                )
            elif decision.block == NormalizationStartBlock.ALREADY_RUNNING:
                self._set_status("LLM-фильтрация уже выполняется", "warning")
            elif decision.block == NormalizationStartBlock.NO_SEGMENTS:
                QMessageBox.warning(
                    self,
                    "LLM-фильтрация",
                    "В транскрипте нет сегментов",
                )
            return

        lesson = self.lesson
        assert lesson is not None
        segments = self._current_source_segments()
        lesson_id = lesson.lesson_id
        try:
            model = self._persist_selected_normalization_model()
        except Exception as exc:
            QMessageBox.warning(self, "LLM-фильтрация", str(exc))
            return
        cloud_consent = None
        if provider == "yandex_ai_studio":
            try:
                cloud_consent = self._request_cloud_consent(
                    lesson_id,
                    model,
                    segments,
                )
            except Exception as exc:
                QMessageBox.warning(self, "Облачная обработка", str(exc))
                return
            if cloud_consent is None:
                self._set_status("Облачная обработка отменена", "warning")
                return

        self.normalization_coordinator.begin(lesson_id)
        token = CancellationToken()
        self._normalization_cancellation = token
        self._normalization_lesson_id = lesson_id
        self._sync_normalization_controls()
        self._set_status(
            f"Фильтрую учебное содержание · {provider_label(provider)} · {model}",
            "working",
        )
        self._launch_normalization_worker(
            lesson_id,
            token,
            model=model,
            force=force,
            source_segments=segments,
            source_artifact="review-buffer",
            retry_indeterminate=retry_indeterminate,
            cloud_consent=cloud_consent,
        )

    def _launch_normalization_worker(
        self,
        lesson_id: str,
        token: CancellationToken,
        **kwargs,
    ) -> None:
        worker = NormalizationWorker(
            self.normalization_service,
            lesson_id=lesson_id,
            cancellation=token,
            **kwargs,
        )
        worker.progress.connect(self._normalization_progress_updated)
        worker.resume_confirmation_required.connect(
            self._normalization_resume_confirmation_required
        )
        worker.succeeded.connect(
            lambda result, expected=lesson_id: self._normalization_ready(
                result,
                expected,
            )
        )
        worker.failed.connect(self._normalization_failed)
        worker.finished.connect(lambda: self._normalization_worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _pump_auto_normalization(self) -> None:
        decision = self.normalization_coordinator.pump_auto(
            NormalizationAutoContext(
                provider=self.config.normalization.provider,
                shutdown_requested=self._shutdown_requested,
                transcription_busy=(
                    self.transcription_worker.busy
                    or self.transcription_queue_coordinator.active is not None
                ),
            )
        )
        if decision.action == NormalizationAutoAction.WAITING_CLOUD_CONSENT:
            self._set_status(
                "Облачная автофильтрация ожидает ручного согласия преподавателя",
                "warning",
            )
            return
        if decision.action != NormalizationAutoAction.START:
            return
        lesson_id = decision.lesson_id
        assert lesson_id is not None
        token = CancellationToken()
        self._normalization_cancellation = token
        self._normalization_lesson_id = lesson_id
        self._sync_normalization_controls()
        self._launch_normalization_worker(lesson_id, token)

    def _normalization_progress_updated(self, progress) -> None:
        self.normalization_coordinator.update_progress(progress)
        self._sync_normalization_controls()

    def _normalization_resume_confirmation_required(self, error) -> None:
        answer = QMessageBox.question(
            self,
            "Повторный облачный запрос",
            str(error) + "\n\nПовторить только неопределённые блоки?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        self.normalization_coordinator.record_resume_confirmation(
            answer == QMessageBox.Yes
        )
        self._set_status(
            "Требуется подтверждение повторного облачного запроса",
            "warning",
        )

    def cancel_normalization(self) -> None:
        if self._normalization_cancellation is None:
            return
        if not self.normalization_coordinator.request_cancel():
            return
        self._normalization_cancellation.cancel()
        self._sync_normalization_controls()
        self._set_status("Отмена нормализации запрошена…", "warning")

    def _normalization_ready(
        self,
        result: NormalizationExecution,
        expected_lesson_id: str,
    ) -> None:
        presentation = build_normalization_ready_presentation(result)
        if (
            self.lesson
            and self.lesson.lesson_id == expected_lesson_id
            and result.transcript.lesson_id == expected_lesson_id
        ):
            self._normalization_execution = result
            self.transcript_workspace.set_result_preview(
                result.transcript.educational_text,
                summary=presentation.preview_summary,
                warnings=result.transcript.quality.warnings,
                select=True,
            )
        self.transcript_workspace.set_process_state(
            presentation.process_title,
            presentation.process_detail,
            tone=presentation.process_tone,
        )
        self._set_status(
            presentation.status_text,
            presentation.status_tone,
        )
        logging.info(
            "event=normalization_gui_ready lesson_id=%s run_id=%s",
            expected_lesson_id,
            result.run.id if result.run else "dry-run",
        )

    def _normalization_failed(self, details: str) -> None:
        logging.error("LLM-фильтрация завершилась ошибкой:\n%s", details)
        presentation = build_normalization_failure_presentation(details)
        self._transcript_primary_action = "retry"
        self.transcript_workspace.set_primary_action(
            "Повторить",
            enabled=True,
        )
        self.transcript_workspace.set_process_state(
            presentation.process_title,
            presentation.message,
            tone=presentation.tone,
        )
        self._set_status(presentation.status_text, presentation.tone)
        QMessageBox.warning(self, "LLM-фильтрация", presentation.message)

    def _normalization_worker_finished(self, worker: Worker) -> None:
        next_action = self.normalization_coordinator.finish_worker()
        self._normalization_cancellation = None
        self._normalization_lesson_id = None
        self._worker_finished(worker)
        self._sync_normalization_controls()
        self._pump_transcription_queue()
        if next_action == NormalizationAfterWorkerAction.RETRY_INDETERMINATE:
            QTimer.singleShot(
                0,
                lambda: self.normalize_current_transcript(retry_indeterminate=True),
            )
        else:
            QTimer.singleShot(0, self._pump_auto_normalization)

    def _normalization_payload(
        self,
    ) -> tuple[int, NormalizedTranscript, list[SourceSegment]] | None:
        if not self.lesson:
            return None
        run = self.normalization_service.runs.latest(self.lesson.lesson_id)
        if not run or not run.artifact_path:
            return None
        artifact = self.content_service.workspace / run.artifact_path
        if not artifact.is_file():
            return None
        transcript = self.normalization_service.load_result(run)
        return run.id or 0, transcript, self._current_source_segments()

    def open_normalization_result(self) -> None:
        payload = self._normalization_payload()
        if payload is None:
            QMessageBox.warning(
                self,
                "LLM-фильтрация",
                "Готовый текстовый результат не найден",
            )
            return
        run_id, transcript, source_segments = payload
        dialog = NormalizationReviewDialog(transcript, source_segments, self)
        outcome = dialog.exec()
        if dialog.restart_requested:
            self.normalize_current_transcript(force=True)
            return
        if outcome != QDialog.Accepted:
            return
        edited_text = dialog.edited_text
        if not edited_text:
            QMessageBox.warning(
                self,
                "LLM-фильтрация",
                "Нельзя применить пустой транскрипт",
            )
            return
        self._apply_normalization_result(
            run_id,
            transcript.lesson_id,
            source_segments,
            edited_text,
        )

    def _apply_normalization_result(
        self,
        run_id: int,
        lesson_id: str,
        source_segments: list[SourceSegment],
        edited_text: str,
    ) -> None:
        self._set_status("Применяю результат как новую ревизию…", "working")
        worker = Worker(
            lambda: self.normalization_service.apply_result(
                run_id,
                current_segments=source_segments,
                edited_text=edited_text,
            )
        )
        worker.succeeded.connect(
            lambda _run, expected=lesson_id, text=edited_text: self._normalization_applied(expected, text)
        )
        worker.failed.connect(self._normalization_failed)
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _normalization_applied(self, lesson_id: str, text: str) -> None:
        if self.lesson and self.lesson.lesson_id == lesson_id:
            self.lesson = self.content_service.get_lesson(lesson_id).lesson
            self._loading_segments = True
            self.transcript.setPlainText(text)
            self._loading_segments = False
            self._summary_dirty = False
            self.approve.setEnabled(False)
            self.publish_button.setEnabled(True)
            self._sync_normalization_controls()
            self.transcript_workspace.select_summary()
            self._go_to(2)
        self._set_status("Результат проверен · транскрипт готов к публикации")

    def reject_normalization_result(self) -> None:
        if not self.lesson:
            return
        run = self.normalization_service.runs.latest(self.lesson.lesson_id)
        if not run:
            return
        answer = QMessageBox.question(
            self,
            "Отклонить нормализацию",
            "Отклонить результат? Исходный транскрипт останется без изменений.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if self._normalization_cancellation:
            self._normalization_cancellation.cancel()
        try:
            self.normalization_service.reject_result(run.id or 0)
        except Exception as exc:
            QMessageBox.warning(self, "LLM-фильтрация", str(exc))
            return
        self._normalization_execution = None
        self._sync_normalization_controls()
        self.transcript_workspace.select_summary()
        self._set_status("Результат нормализации отклонён", "warning")

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
        payload = "\n".join(f"• {path}" for path in publication_payload_files(self.lesson))
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

    def _apply_latex_monitor_presentation(
        self,
        presentation: LatexMonitorPresentation,
    ) -> None:
        self.latex_monitor_status.setText(presentation.monitor_status)
        if presentation.log_text is not None:
            self.compilation_log.setPlainText(presentation.log_text)
        if presentation.replace_previews:
            self.pdf_previews.clear()
            for path in presentation.preview_paths:
                item = QListWidgetItem(path.name)
                item.setData(256, str(path.resolve()))
                self.pdf_previews.addItem(item)
        self._set_status(presentation.app_status, presentation.tone)
        if presentation.dialog_message is None:
            return
        if presentation.dialog_kind == "critical":
            QMessageBox.critical(
                self,
                presentation.dialog_title or "Ошибка",
                presentation.dialog_message,
            )
        else:
            QMessageBox.information(
                self,
                presentation.dialog_title or "Готово",
                presentation.dialog_message,
            )

    def toggle_latex_monitor(self, enabled: bool) -> None:
        self.latex_monitor_coordinator.set_enabled(enabled)
        if enabled:
            self.latex_poll_timer.start()
        else:
            self.latex_poll_timer.stop()
        self._apply_latex_monitor_presentation(
            build_latex_monitor_toggle_presentation(
                enabled=enabled,
                poll_seconds=self.config.latex.poll_seconds,
            )
        )
        if enabled:
            self.scan_remote_latex(trigger=LatexMonitorScanTrigger.ENABLE)

    def scan_remote_latex(
        self,
        _checked: bool = False,
        *,
        periodic: bool = False,
        trigger: LatexMonitorScanTrigger | None = None,
    ) -> None:
        del _checked
        selected_trigger = trigger or (
            LatexMonitorScanTrigger.PERIODIC
            if periodic
            else LatexMonitorScanTrigger.MANUAL
        )
        decision = self.latex_monitor_coordinator.request_scan(selected_trigger)
        if not decision.should_start:
            return

        from ..latex import RemoteLatexService

        self._apply_latex_monitor_presentation(
            build_latex_monitor_scanning_presentation()
        )

        def scan():
            with self.content_service.activity("latex-monitor"):
                service = RemoteLatexService(self.config.repository, self.config.latex)
                for lesson in self.pipeline.store.list():
                    if service.is_ready(lesson):
                        return service.compile_lesson(
                            lesson,
                            cache_dir=self.pipeline.lesson_dir(lesson) / "latex-cache",
                        )
            return None

        worker = Worker(scan)
        worker.succeeded.connect(self._remote_compilation_ready)
        worker.failed.connect(self._latex_monitor_failed)
        worker.finished.connect(lambda: self._latex_monitor_worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _remote_compilation_ready(self, remote_result) -> None:
        if remote_result is None:
            self._apply_latex_monitor_presentation(
                build_latex_monitor_no_update_presentation()
            )
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
        self._apply_latex_monitor_presentation(
            build_latex_monitor_result_presentation(
                branch=remote_result.branch,
                success=result.success,
                attempt=lesson.latex.attempt,
                max_attempts=self.config.latex.max_attempts,
                errors=result.errors,
                warnings=result.warnings,
                preview_paths=result.preview_files,
            )
        )

    def _latex_monitor_failed(self, details: str) -> None:
        logging.error(details)
        self._apply_latex_monitor_presentation(
            build_latex_monitor_failure_presentation(details)
        )

    def _latex_monitor_worker_finished(self, worker: Worker) -> None:
        self.latex_monitor_coordinator.finish_scan()
        self._worker_finished(worker)

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
        if self._normalization_cancellation is not None:
            self._normalization_cancellation.cancel()
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

    def _apply_recording_tick_presentation(
        self,
        presentation: RecordingTickPresentation,
    ) -> None:
        self.duration.setText(presentation.duration_text)
        if presentation.microphone_level_percent is not None:
            self.mic_level.setValue(presentation.microphone_level_percent)
        if presentation.system_level_percent is not None:
            self.system_level.setValue(presentation.system_level_percent)
        if presentation.health_text is not None:
            self.recording_health_label.setText(presentation.health_text)
        if presentation.status_message is not None and presentation.status_tone is not None:
            self._set_status(presentation.status_message, presentation.status_tone)
        if presentation.warning_log is not None:
            logging.warning("Контроль записи: %s", presentation.warning_log)

    def _set_recording_panel_phase(self, phase: RecordingPanelPhase) -> None:
        visual = recording_panel_visual(phase)
        self.recording_state_label.setText(visual.text)
        self.recording_state_label.setProperty("active", visual.active)
        refresh_style(self.recording_state_label)

    def _tick(self) -> None:
        self.recording_seconds += 1
        assessment = None
        if self.recorder and self.recorder.active:
            assessment = self.recording_health_monitor.assess(
                RecordingHealthSample.from_runtime(
                    elapsed_seconds=self.recording_seconds,
                    levels=self.recorder.levels,
                    health=self.recorder.health,
                )
            )
        presentation = build_recording_tick_presentation(
            self.recording_seconds,
            assessment,
        )
        self._apply_recording_tick_presentation(presentation)
        if assessment is not None and assessment.action == RecordingHealthAction.STOP:
            self._stop_recording_async(
                assessment.stop_reason or "Контроль записи запросил безопасную остановку"
            )
            return



def main(window_type: type[MainWindow] = MainWindow) -> None:
    if "--version" in sys.argv:
        from .. import __version__

        print(__version__)
        return
    if "--release-smoke" in sys.argv:
        from ..runtime import build_identity

        print(json.dumps(build_identity().to_dict(), ensure_ascii=False))
        return
    force_setup = "--setup" in sys.argv
    if force_setup:
        sys.argv.remove("--setup")
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_config_path()
    config = AppConfig.load(config_path)
    configure_logging(config.workspace)
    enable_native_fault_handler(config.workspace)
    install_exception_hook(config.workspace)
    app = QApplication(sys.argv)
    install_qt_message_handler()
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
        enable_native_fault_handler(config.workspace)
    window = window_type(config_path)
    install_exception_hook(
        config.workspace,
        activity_provider=lambda: {
            "recording_active": bool(window.recorder and window.recorder.active),
            "transcription_active": bool(window.transcription_worker.busy),
        },
    )
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
