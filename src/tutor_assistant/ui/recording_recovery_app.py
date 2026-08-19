from __future__ import annotations

import logging

from PySide6.QtWidgets import QMessageBox

from ..application.recording_recovery import (
    RecordingRecoveryOutcome,
    RecordingRecoveryState,
    RecoverRecordingUseCase,
)
from ..audio_files import finalize_readable_audio
from ..domain import Lesson
from ..recording import (
    RecordingResult,
    find_recoverable_recordings,
    recover_recording,
)
from . import app as base_app
from .recording_finalize_app import MainWindow as RecordingFinalizeMainWindow
from .shutdown_app import MainWindow as ShutdownMainWindow
from .workspace_sync import WorkspaceSyncMixin


class MainWindow(WorkspaceSyncMixin, RecordingFinalizeMainWindow, ShutdownMainWindow):
    """Production window with synchronized workspace and recording recovery."""

    def __init__(self, config_path):
        super().__init__(config_path)
        self.recover_recording_use_case = RecoverRecordingUseCase(
            discoverer=find_recoverable_recordings,
            recoverer=recover_recording,
            lesson_lookup=self.pipeline.store.get,
            lesson_saver=self._save_recovered_lesson,
            result_finalizer=self._finalize_recovered_result,
        )

    def _save_recovered_lesson(
        self,
        lesson: Lesson,
        fields: tuple[str, ...],
    ) -> object:
        return self.pipeline.save_state(lesson, *fields)

    @staticmethod
    def _finalize_recovered_result(
        result: RecordingResult,
        lesson: Lesson,
    ) -> RecordingResult:
        return finalize_readable_audio(
            result,
            lesson.student.full_name,
            lesson.lesson_date,
        )

    def _offer_recovery(self) -> None:
        try:
            self._recovery_sessions = list(
                reversed(self.recover_recording_use_case.discover(self.config.workspace))
            )
        except Exception:
            logging.exception("Не удалось найти незавершённые записи")
            self._set_status("Не удалось проверить незавершённые записи", "warning")
            return
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
        worker = base_app.Worker(self.recover_recording_use_case.recover, directory)
        worker.purpose = "recording-recovery"
        worker.succeeded.connect(self._recovery_outcome_ready)
        worker.failed.connect(self._recovery_transport_failed)
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _recovery_outcome_ready(self, outcome: RecordingRecoveryOutcome) -> None:
        if outcome.state == RecordingRecoveryState.FAILED:
            self._present_recovery_failed(outcome)
            return

        result = outcome.result
        if result is None:
            self._recovery_transport_failed(
                "Application recovery returned a successful outcome without RecordingResult"
            )
            return

        self.audio_path.setText(str(result.mixed_file))
        self.recording_workflow.reset()
        self._refresh_quick_readiness()
        self.student_content_page.refresh_if_loaded()
        self._refresh_teacher_cockpit()
        self._sync_parallel_review_ui()

        if outcome.state == RecordingRecoveryState.AUDIO_ONLY:
            self._set_status("Аудиозапись восстановлена; карточка занятия не найдена", "warning")
            message = (
                "Аудиозапись восстановлена, но связанная карточка занятия не найдена:\n"
                f"{result.mixed_file}"
            )
        else:
            self._set_status("Аудиозапись восстановлена")
            message = f"Запись восстановлена:\n{result.mixed_file}"

        QMessageBox.information(self, "Восстановление", message)
        self._offer_next_recovery()

    def _present_recovery_failed(self, outcome: RecordingRecoveryOutcome) -> None:
        details = outcome.error or "Неизвестная ошибка восстановления записи"
        logging.error("Ошибка application recovery:\n%s", details)

        if outcome.result is None:
            self.recording_workflow.mark_recovery_required()
            self._set_status("Запись по-прежнему требует восстановления", "error")
            message = (
                "Не удалось восстановить аудио из сохранённых чанков. "
                "Исходные данные оставлены для повторной попытки.\n\n"
                + details[-2000:]
            )
        else:
            self.recording_workflow.mark_failed()
            self.audio_path.setText(str(outcome.result.mixed_file))
            self._set_status(
                "Аудио восстановлено, но карточка занятия не оформлена",
                "error",
            )
            message = (
                f"Аудиофайл восстановлен: {outcome.result.mixed_file}\n\n"
                "Не удалось завершить оформление занятия:\n"
                + details[-1800:]
            )

        self._refresh_quick_readiness()
        self.student_content_page.refresh_if_loaded()
        self._refresh_teacher_cockpit()
        self._sync_parallel_review_ui()
        QMessageBox.critical(self, "Ошибка восстановления", message)
        self._offer_next_recovery()

    def _recovery_transport_failed(self, details: str) -> None:
        logging.error("Unexpected recording recovery worker failure:\n%s", details)
        self.recording_workflow.mark_recovery_required()
        self._set_status("Ошибка фонового восстановления записи", "error")
        QMessageBox.critical(
            self,
            "Ошибка восстановления",
            details[-2000:],
        )
        self._offer_next_recovery()


def main() -> None:
    base_app.main(MainWindow)


if __name__ == "__main__":
    main()
