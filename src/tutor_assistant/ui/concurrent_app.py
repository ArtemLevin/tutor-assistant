from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QLabel, QListWidgetItem, QMessageBox, QPushButton

from ..content import ContentConflictError, ContentNotFoundError
from ..domain import JobStatus, Lesson
from ..latex import RemoteRepositoryUnavailable
from . import app as base_app
from .background import (
    BackgroundTaskPurpose,
    BackgroundTaskResult,
    BackgroundTaskSpec,
    BackgroundTaskState,
    BusyPolicy,
)
from .background_tasks import BackgroundTaskCoordinator
from .parallel_review import (
    ParallelReviewPolicy,
    ProcessingAction,
    parallel_context_text,
    processing_action,
)
from .theme import set_button_kind


class MainWindow(base_app.MainWindow):
    """Main window with independent recording and transcript-review contexts."""

    def __init__(self, config_path: Path) -> None:
        super().__init__(config_path)
        self.background_tasks = BackgroundTaskCoordinator(
            self.content_service,
            self.workers,
            parent=self,
        )
        self.parallel_context_label = QLabel()
        self.parallel_context_label.setObjectName("muted")
        self.parallel_context_label.setWordWrap(True)
        self.parallel_context_label.setVisible(False)
        self.header_layout.addWidget(self.parallel_context_label, 0, Qt.AlignVCenter)

        self.header_stop_button = set_button_kind(QPushButton("■ Завершить запись"), "danger")
        self.header_stop_button.setToolTip("Завершить текущую запись, не закрывая проверяемый транскрипт")
        self.header_stop_button.clicked.connect(self.stop_recording)
        self.header_stop_button.setVisible(False)
        self.header_layout.addWidget(self.header_stop_button, 0, Qt.AlignVCenter)
        self._sync_parallel_review_ui()

    def _run_content_task(self, callable_, succeeded, failed) -> None:
        if not hasattr(self, "background_tasks"):
            super()._run_content_task(callable_, succeeded, failed)
            return
        self.background_tasks.submit(
            BackgroundTaskSpec(
                purpose=BackgroundTaskPurpose.CONTENT_BROWSER,
                operation=callable_,
                busy_policy=BusyPolicy.FAIL,
                allow_parallel=True,
                handled_exceptions=(ContentConflictError, ContentNotFoundError),
            ),
            on_success=lambda result: succeeded(result.payload),
            on_busy=lambda result: failed(result.reason or "Хранилище временно занято"),
            on_handled=lambda result: failed(result.reason or "Операция недоступна"),
            on_failure=failed,
            on_finished=self._maybe_finish_shutdown,
        )

    def _run_content_maintenance(self) -> None:
        if (
            not hasattr(self, "background_tasks")
            or not self.config.content.maintenance_enabled
            or self._shutdown_requested
            or (self.recorder and self.recorder.active)
        ):
            return

        self.background_tasks.submit(
            BackgroundTaskSpec(
                purpose=BackgroundTaskPurpose.CONTENT_MAINTENANCE,
                operation=lambda: self.content_service.run_maintenance(
                    auto_repair=self.config.content.auto_repair,
                    purge_expired=self.config.content.auto_purge_trash,
                    cleanup_temporary=self.config.content.auto_cleanup_temporary,
                    temporary_retention=timedelta(hours=self.config.content.temporary_retention_hours),
                    backup_enabled=self.config.content.backup_enabled,
                    backup_interval=timedelta(hours=self.config.content.backup_interval_hours),
                    backup_retention_count=self.config.content.backup_retention_count,
                    max_lessons=self.config.content.maintenance_max_lessons_per_cycle,
                    max_seconds=self.config.content.maintenance_max_seconds,
                    apply_max_seconds=self.config.content.maintenance_apply_max_seconds,
                ),
                busy_policy=BusyPolicy.SKIP,
            ),
            on_success=lambda result: self._content_maintenance_ready(result.payload),
            on_busy=self._content_maintenance_busy,
            on_failure=self._content_maintenance_failed,
            on_finished=self._maybe_finish_shutdown,
        )

    def _content_maintenance_busy(self, result: BackgroundTaskResult[object]) -> None:
        logging.info("Цикл обслуживания архива пропущен: %s", result.reason)
        self._set_status(
            "Цикл обслуживания пропущен: выполняется рабочая операция",
            "warning",
        )

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

        self.background_tasks.submit(
            BackgroundTaskSpec(
                purpose=BackgroundTaskPurpose.LATEX_COMPILATION,
                operation=lambda: LatexCompiler(self.config.latex).compile(path),
                activity="latex-compilation",
                busy_policy=BusyPolicy.FAIL,
                manually_requested=True,
            ),
            on_success=self._local_compilation_task_ready,
            on_busy=self._local_compilation_busy,
            on_failure=lambda details: self._operation_failed("compile", details),
            on_finished=self._maybe_finish_shutdown,
        )

    def _local_compilation_task_ready(
        self,
        result: BackgroundTaskResult[object],
    ) -> None:
        if result.payload is None:
            self._operation_failed("compile", "Компиляция завершилась без результата")
            return
        self._local_compilation_ready(result.payload)

    def _local_compilation_busy(self, result: BackgroundTaskResult[object]) -> None:
        self.compile_tex_button.setEnabled(True)
        message = result.reason or "Хранилище временно занято"
        self._set_status("Компиляция отложена: хранилище занято", "warning")
        QMessageBox.warning(self, "Компиляция", message)

    def scan_remote_latex(
        self,
        _checked: bool = False,
        *,
        manually_requested: bool | None = None,
    ) -> None:
        if manually_requested is None:
            manually_requested = isinstance(self.sender(), QPushButton)
        self.latex_monitor_status.setText("Проверяю удалённые ветки…")
        self._set_status("Проверяю ветки занятий…", "working")

        self.background_tasks.submit(
            BackgroundTaskSpec(
                purpose=BackgroundTaskPurpose.LATEX_MONITOR,
                operation=self.pipeline.scan_remote_latex,
                busy_policy=BusyPolicy.DEFER,
                manually_requested=manually_requested,
                none_is_no_changes=True,
                retry_allowed=lambda: bool(manually_requested or self.auto_latex.isChecked()),
                handled_exceptions=(RemoteRepositoryUnavailable,),
                handled_exception_retryable=True,
            ),
            on_success=self._remote_monitor_ready,
            on_busy=self._remote_monitor_busy,
            on_handled=self._remote_monitor_unavailable,
            on_failure=lambda details: self._operation_failed("latex-monitor", details),
            on_finished=self._maybe_finish_shutdown,
        )

    def _remote_monitor_ready(
        self,
        result: BackgroundTaskResult[object],
    ) -> None:
        if result.state == BackgroundTaskState.NO_CHANGES:
            self.latex_monitor_status.setText("Новых TEX-файлов нет")
            self._set_status("Новых TEX-файлов нет")
            return
        if result.payload is None:
            self._operation_failed(
                "latex-monitor",
                "Фоновая проверка LaTeX завершилась без результата компиляции",
            )
            return
        super()._remote_compilation_ready(result.payload)

    def _remote_monitor_unavailable(
        self,
        result: BackgroundTaskResult[object],
    ) -> None:
        message = result.reason or "GitHub временно недоступен"
        self.latex_monitor_status.setText("GitHub временно недоступен; повторю проверку автоматически")
        self._set_status(
            "Проверка LaTeX отложена из-за временной сетевой ошибки",
            "warning",
        )
        if result.manually_requested:
            QMessageBox.warning(self, "Проверка LaTeX", message)

    def _remote_monitor_busy(
        self,
        result: BackgroundTaskResult[object],
    ) -> None:
        blocker = result.blocking_activity
        if blocker == "content-maintenance":
            description = "обслуживается архив"
        elif blocker:
            description = f"хранилище занято: {blocker}"
        else:
            description = "хранилище временно занято"
        self.latex_monitor_status.setText(f"Проверка отложена: {description}")
        self._set_status(
            "Проверка LaTeX будет повторена после освобождения архива",
            "warning",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)
        if self._shutdown_requested or event.isAccepted():
            self.background_tasks.begin_shutdown()

    @property
    def review_lesson(self) -> Lesson | None:
        """Explicit name for the lesson currently opened in the review/publish UI."""

        return self.lesson

    @review_lesson.setter
    def review_lesson(self, lesson: Lesson | None) -> None:
        self.lesson = lesson

    def _parallel_policy(self) -> ParallelReviewPolicy:
        return ParallelReviewPolicy(
            recording_active=bool(self.recorder and self.recorder.active),
            recording_stopping=self._recording_stop_started,
        )

    def _sync_parallel_review_ui(self) -> None:
        if not hasattr(self, "header_stop_button"):
            return
        policy = self._parallel_policy()
        recording = self.recording_lesson if policy.recording_busy else None
        review = self.review_lesson
        text = parallel_context_text(
            recording_student=recording.student.full_name if recording else None,
            recording_topic=recording.topic if recording else None,
            review_student=review.student.full_name if review else None,
            review_topic=review.topic if review else None,
            elapsed_seconds=self.recording_seconds,
        )
        self.parallel_context_label.setText(text)
        self.parallel_context_label.setVisible(bool(text))
        self.header_stop_button.setVisible(policy.recording_busy)
        self.header_stop_button.setEnabled(
            bool(self.recorder and self.recorder.active) and not self._recording_stop_started
        )
        stop_text = "Сохраняю запись…" if self._recording_stop_started else "■ Завершить запись"
        self.header_stop_button.setText(stop_text)
        self.play_segment_button.setEnabled(policy.audio_playback_allowed and bool(review))

    def _load_review_lesson(self, lesson: Lesson, *, restore_form: bool | None = None) -> None:
        policy = self._parallel_policy()
        restore_form = policy.restore_recording_form if restore_form is None else restore_form

        self.review_lesson = lesson
        self._loading_segments = True
        self._summary_dirty = False
        self.transcript.clear()
        self.segment_table.setRowCount(0)

        if restore_form:
            self.audio_path.clear()
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
        self._set_status(f"Занятие открыто · {lesson.student.full_name}")
        self._sync_parallel_review_ui()

    def _load_lesson(self, lesson: Lesson) -> None:
        # Startup recovery may restore the form; parallel queue review must not touch it.
        self._load_review_lesson(lesson)

    def _open_processing_item(self, item: QListWidgetItem) -> None:
        job = self.transcription_queue.get(str(item.data(256)))
        if job is None:
            return

        action = processing_action(job.status.value)
        if action == ProcessingAction.RETRY:
            answer = QMessageBox.question(
                self,
                "Ошибка транскрибации",
                (job.error or "Неизвестная ошибка") + "\n\nПовторить транскрибацию?",
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
        if action == ProcessingAction.WAIT:
            self._set_status("Транскрипт ещё обрабатывается", "working")
            return

        # A ready transcript is text-only and is safe to open during another recording.
        self._load_review_lesson(job.lesson, restore_form=False)

    def play_selected_segment(self, _index=None) -> None:
        if not self._parallel_policy().audio_playback_allowed:
            QMessageBox.warning(
                self,
                "Воспроизведение отключено",
                "Во время записи нельзя воспроизводить старое аудио. "
                "Оно попадёт в WASAPI Loopback. Текст транскрипта можно "
                "читать, исправлять и подтверждать.",
            )
            return
        super().play_selected_segment(_index)

    def _play_preflight_track(self, source: str) -> None:
        if not self._parallel_policy().audio_playback_allowed:
            QMessageBox.warning(
                self,
                "Воспроизведение отключено",
                "Сначала завершите текущую запись.",
            )
            return
        super()._play_preflight_track(source)

    def start_recording(self) -> None:
        # The base window stops application-owned media before opening loopback capture.
        super().start_recording()
        self._sync_parallel_review_ui()

    def _stop_recording_async(self, reason: str | None = None) -> None:
        super()._stop_recording_async(reason)
        self._sync_parallel_review_ui()

    def _recording_ready_impl(
        self,
        result,
        recorded_lesson: Lesson,
        source_recorder,
        reason: str | None = None,
    ) -> None:
        review_before = self.review_lesson
        super()._recording_ready_impl(result, recorded_lesson, source_recorder, reason)
        # Finalizing lesson B must not replace or clear lesson A opened for review.
        if review_before is not None:
            self.review_lesson = review_before
        self._sync_parallel_review_ui()

    def _recording_stop_failed(self, details: str) -> None:
        super()._recording_stop_failed(details)
        self._sync_parallel_review_ui()

    def _tick(self) -> None:
        super()._tick()
        self._sync_parallel_review_ui()


def main() -> None:
    # Reuse the established startup/setup workflow while injecting the safe window implementation.
    base_app.MainWindow = MainWindow
    base_app.main()


if __name__ == "__main__":
    main()
