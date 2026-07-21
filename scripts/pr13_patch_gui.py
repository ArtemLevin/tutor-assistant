from __future__ import annotations

from pathlib import Path

path = Path("src/tutor_assistant/ui/concurrent_app.py")
text = path.read_text(encoding="utf-8")

imports_end = text.index("\n\nclass MainWindow")
new_imports = '''from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QLabel, QListWidgetItem, QMessageBox, QPushButton

from ..domain import JobStatus, Lesson
from . import app as base_app
from .background import (
    BackgroundTaskPurpose,
    BackgroundTaskResult,
    BackgroundTaskSpec,
    BackgroundTaskState,
    BusyPolicy,
    scan_remote_latex,
)
from .background_tasks import BackgroundTaskCoordinator
from .parallel_review import (
    ParallelReviewPolicy,
    ProcessingAction,
    parallel_context_text,
    processing_action,
)
from .theme import set_button_kind'''
text = new_imports + text[imports_end:]

start = text.index("class MainWindow(base_app.MainWindow):\n")
end = text.index("    @property\n", start)
prefix = '''class MainWindow(base_app.MainWindow):
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

        self.header_stop_button = set_button_kind(
            QPushButton("■ Завершить запись"), "danger"
        )
        self.header_stop_button.setToolTip(
            "Завершить текущую запись, "
            "не закрывая проверяемый транскрипт"
        )
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
            ),
            on_success=lambda result: succeeded(result.payload),
            on_busy=lambda result: failed(result.reason or "Хранилище временно занято"),
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
                operation=lambda: self.content_service.run_maintenance_uncoordinated(
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
                ),
                activity="content-maintenance",
                exclusive=True,
                ttl=timedelta(minutes=5),
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
                operation=lambda: scan_remote_latex(
                    self.config.repository,
                    self.config.latex,
                    self.pipeline.store.list(),
                    lambda lesson: self.pipeline.lesson_dir(lesson) / "latex-cache",
                ),
                activity="latex-monitor",
                busy_policy=BusyPolicy.DEFER,
                manually_requested=manually_requested,
                none_is_no_changes=True,
                retry_allowed=lambda: bool(
                    manually_requested or self.auto_latex.isChecked()
                ),
            ),
            on_success=self._remote_monitor_ready,
            on_busy=self._remote_monitor_busy,
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

'''
text = text[:start] + prefix + text[end:]
path.write_text(text, encoding="utf-8")
