from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox

from ..application import (
    ShutdownCloseAction,
    ShutdownCoordinator,
    ShutdownDrainAction,
    ShutdownRuntimeSnapshot,
)
from . import app as base_app
from .concurrent_app import MainWindow as ConcurrentMainWindow


class MainWindow(ConcurrentMainWindow):
    """Production adapter executing Qt-free shutdown lifecycle decisions."""

    def __init__(self, config_path):
        # Base MainWindow connects worker signals to _maybe_finish_shutdown while it is
        # still constructing, so the coordinator must exist before super().__init__().
        self.shutdown_coordinator = ShutdownCoordinator()
        super().__init__(config_path)

    def _shutdown_runtime_snapshot(self) -> ShutdownRuntimeSnapshot:
        return ShutdownRuntimeSnapshot(
            recording_active=bool(self.recorder and self.recorder.active),
            recording_stop_in_flight=self._recording_stop_started,
            workers_running=any(worker.isRunning() for worker in self.workers),
            transcription_busy=self.transcription_worker.busy,
            transcription_running=self.transcription_worker.isRunning(),
            normalization_cancellable=self._normalization_cancellation is not None,
        )

    def _begin_background_shutdown(self) -> None:
        if hasattr(self, "backup_coordinator"):
            self.backup_coordinator.request_shutdown()
        if hasattr(self, "backup_maintenance_timer"):
            self.backup_maintenance_timer.stop()
        if hasattr(self, "background_tasks"):
            self.background_tasks.begin_shutdown()

    def _begin_shutdown_drain(self, snapshot: ShutdownRuntimeSnapshot) -> None:
        plan = self.shutdown_coordinator.confirm_close(snapshot, confirmed=True)
        if not plan.begin_draining:
            return

        # Compatibility mirrors for lower production adapters. The coordinator is
        # the source of truth; these flags remain only until those adapters stop
        # reading the historical attributes.
        self._shutdown_requested = True
        self._begin_background_shutdown()

        if plan.cancel_normalization and self._normalization_cancellation is not None:
            self._normalization_cancellation.cancel()
        if plan.shutdown_transcription:
            self.transcription_worker.shutdown()
        if plan.quiesce_runtime:
            self.timer.stop()
            self.latex_poll_timer.stop()
            self.content_maintenance_timer.stop()
            self.quick_countdown_timer.stop()
            self.start_button.setEnabled(False)
            self.quick_start_button.setEnabled(False)
            self._set_status("Завершаю текущие операции…", "working")
        if plan.finalize_recording:
            self._stop_recording_async(
                "Приложение закрывается; запись корректно завершается"
            )

    def closeEvent(self, event: QCloseEvent) -> None:
        self.playback_controller.stop(clear_source=True)
        snapshot = self._shutdown_runtime_snapshot()
        decision = self.shutdown_coordinator.request_close(snapshot)

        if decision.action == ShutdownCloseAction.ACCEPT:
            self._begin_background_shutdown()
            event.accept()
            return
        if decision.action == ShutdownCloseAction.IGNORE:
            event.ignore()
            return
        if decision.action == ShutdownCloseAction.TRY_IMMEDIATE:
            self._begin_background_shutdown()
            self.transcription_worker.shutdown()
            stopped = self.transcription_worker.wait(decision.transcription_wait_ms or 0)
            self.shutdown_coordinator.complete_immediate_shutdown(
                transcription_stopped=stopped
            )
            if stopped:
                self._shutdown_ready = True
                event.accept()
            else:
                self._shutdown_requested = True
                event.ignore()
            return

        if decision.action != ShutdownCloseAction.PROMPT:
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
            self.shutdown_coordinator.confirm_close(snapshot, confirmed=False)
            event.ignore()
            return

        event.ignore()
        self._begin_shutdown_drain(self._shutdown_runtime_snapshot())
        self._maybe_finish_shutdown()

    def _maybe_finish_shutdown(self) -> None:
        action = self.shutdown_coordinator.observe_drain(
            self._shutdown_runtime_snapshot()
        )
        if action == ShutdownDrainAction.SCHEDULE_CLOSE:
            self._shutdown_ready = True
            QTimer.singleShot(0, self.close)


def main() -> None:
    base_app.main(MainWindow)


if __name__ == "__main__":
    main()
