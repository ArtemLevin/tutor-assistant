from __future__ import annotations

import json
import logging

from PySide6.QtWidgets import QMessageBox

from ..application.recording_stop import (
    RecordingStopOutcome,
    RecordingStopSession,
    RecordingStopState,
    StopRecordingUseCase,
)
from ..audio_files import finalize_readable_audio
from ..domain import Lesson
from ..recording import RecordingResult
from . import app as base_app
from .audio_resilient_app import MainWindow as AudioResilientMainWindow
from .theme import refresh_style


class MainWindow(AudioResilientMainWindow):
    """Production window with application-owned stop/finalize recording semantics."""

    def __init__(self, config_path):
        super().__init__(config_path)
        self.stop_recording_use_case = StopRecordingUseCase(
            self.pipeline,
            result_finalizer=self._finalize_recording_result,
        )
        self._active_stop_session: RecordingStopSession | None = None

    @staticmethod
    def _finalize_recording_result(
        result: RecordingResult,
        lesson: Lesson,
    ) -> RecordingResult:
        return finalize_readable_audio(
            result,
            lesson.student.full_name,
            lesson.lesson_date,
        )

    def _stop_recording_async(self, reason: str | None = None) -> None:
        if not self.recording_workflow.begin_stop(self._recording_runtime_state()):
            return
        if self._recording_stop_started or not self.recorder or not self.recording_lesson:
            self.recording_workflow.observe_runtime(self._recording_runtime_state())
            return

        session = RecordingStopSession(
            lesson=self.recording_lesson,
            recorder=self.recorder,
            lease=self._recording_lease,
        )
        self._active_stop_session = session
        self._recording_stop_started = True
        self.timer.stop()
        self.stop_button.setEnabled(False)
        self.recording_state_label.setText("СОХРАНЯЮ ЗАПИСЬ…")
        self._set_status("Сохраняю и проверяю аудиодорожки…", "working")
        if reason:
            logging.warning("Аварийное завершение записи: %s", reason)
        else:
            logging.info("Завершение записи запрошено через application use case")

        worker = base_app.Worker(self.stop_recording_use_case.stop, session)
        worker.purpose = "recording-stop"
        worker.succeeded.connect(
            lambda outcome, expected=session: self._recording_stop_outcome_ready(
                outcome,
                expected,
                reason,
            )
        )
        worker.failed.connect(
            lambda details, expected=session: self._recording_stop_transport_failed(
                details,
                expected,
            )
        )
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()
        self.recording_workflow.observe_runtime(self._recording_runtime_state())
        self._sync_parallel_review_ui()

    def _recording_stop_transport_failed(
        self,
        details: str,
        expected: RecordingStopSession,
    ) -> None:
        """Last-resort guard for an unexpected worker/use-case transport failure."""

        logging.error("Unexpected recording stop worker failure:\n%s", details)
        self._recording_stop_outcome_ready(
            RecordingStopOutcome.recovery_required(expected, details),
            expected,
            None,
        )

    def _recording_stop_outcome_ready(
        self,
        outcome: RecordingStopOutcome,
        expected: RecordingStopSession,
        reason: str | None,
    ) -> None:
        if self._active_stop_session is not expected:
            logging.error("Игнорируется устаревший результат завершения записи")
            return
        if self.recording_lesson is not expected.lesson or self.recorder is not expected.recorder:
            logging.error("Игнорируется результат записи с устаревшим runtime-контекстом")
            self._active_stop_session = None
            return

        self._recording_lease = None
        self._active_stop_session = None
        try:
            if outcome.state == RecordingStopState.RECORDED:
                self._present_recording_completed(outcome, reason)
            elif outcome.state == RecordingStopState.RECOVERY_REQUIRED:
                self._present_recording_recovery_required(outcome.error or "Неизвестная ошибка записи")
            else:
                self._present_recording_finalization_failed(outcome)
        finally:
            self.audio_output_format.setEnabled(True)
            self._refresh_teacher_cockpit()
            self._sync_parallel_review_ui()
            self._maybe_finish_shutdown()

    def _present_recording_completed(
        self,
        outcome: RecordingStopOutcome,
        reason: str | None,
    ) -> None:
        result = outcome.result
        if result is None:
            raise RuntimeError("Финализация записи завершилась без RecordingResult")

        recorded_lesson = outcome.lesson
        review_before = self.review_lesson
        self._update_scheduled_occurrence(
            "completed",
            lesson_id=recorded_lesson.lesson_id,
            clear=True,
        )
        self.start_button.setEnabled(True)
        self.quick_start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.test_devices_button.setEnabled(True)
        self.quick_start_button.setText("Начать занятие")
        self.recording_state_label.setText("ЗАПИСЬ СОХРАНЕНА")
        self.recording_state_label.setProperty("active", False)
        refresh_style(self.recording_state_label)

        warnings: list[str] = []
        quality_ready = None
        try:
            quality = json.loads(result.quality_report.read_text(encoding="utf-8"))
            if isinstance(quality, dict):
                warnings.extend(str(item) for item in quality.get("warnings", []))
                quality_ready = quality.get("ready")
        except (OSError, json.JSONDecodeError):
            logging.exception("Не удалось прочитать отчёт качества завершённой записи")
            warnings.append("Не удалось прочитать отчёт качества аудио")
        if reason:
            warnings.insert(0, reason)

        if warnings:
            self._set_status("Запись сохранена с предупреждениями", "warning")
            QMessageBox.warning(self, "Проверка записи", "\n".join(warnings))
        else:
            self._set_status("Запись сохранена и проверена")
        logging.info(
            "Запись сохранена через application use case: %s; quality_ready=%s",
            result.mixed_file,
            quality_ready,
        )

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
            if review_before is not None:
                self.review_lesson = review_before

        self.recording_workflow.mark_completed()

    def _present_recording_recovery_required(self, details: str) -> None:
        logging.error(details)
        self._recording_stop_started = False
        self.recorder = None
        self.recording_lesson = None
        self._update_scheduled_occurrence("recording_failed", clear=True)
        self.start_button.setEnabled(True)
        self.quick_start_button.setEnabled(True)
        self.test_devices_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._quick_auto_transcribe_active = False
        self._refresh_quick_readiness()
        self.recording_state_label.setText("ЗАПИСЬ ТРЕБУЕТ ВОССТАНОВЛЕНИЯ")
        self.recording_state_label.setProperty("active", False)
        refresh_style(self.recording_state_label)
        self._set_status("Запись сохранена частично; доступно восстановление", "error")
        self.recording_workflow.mark_recovery_required()
        QMessageBox.critical(
            self,
            "Ошибка завершения записи",
            "Доступные аудиочанки сохранены. После перезапуска приложение предложит восстановление.\n\n"
            + details[-2000:],
        )

    def _present_recording_finalization_failed(self, outcome: RecordingStopOutcome) -> None:
        details = outcome.error or "Неизвестная ошибка финализации записи"
        result = outcome.result
        logging.error("Ошибка финализации записанного занятия\n%s", details)
        self._recording_stop_started = False
        self.recorder = None
        self.recording_lesson = None
        self._update_scheduled_occurrence("recording_failed", clear=True)
        self.start_button.setEnabled(True)
        self.quick_start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.test_devices_button.setEnabled(True)
        self._quick_auto_transcribe_active = False
        self._refresh_quick_readiness()
        self.recording_state_label.setText("ЗАПИСЬ СОХРАНЕНА С ОШИБКОЙ")
        self.recording_state_label.setProperty("active", False)
        refresh_style(self.recording_state_label)
        self._set_status("Аудио сохранено, оформление занятия завершилось с ошибкой", "error")
        self.recording_workflow.mark_failed()
        audio_hint = f"Аудиофайл сохранён: {result.mixed_file}\n\n" if result is not None else ""
        QMessageBox.critical(
            self,
            "Ошибка оформления записи",
            audio_hint + details[-2000:],
        )


def main() -> None:
    base_app.MainWindow = MainWindow
    base_app.main()


if __name__ == "__main__":
    main()
