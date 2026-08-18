from __future__ import annotations

from pathlib import Path


APP = Path("src/tutor_assistant/ui/app.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"{label}: start marker not found")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:start_index] + replacement + text[end_index:]


app = APP.read_text(encoding="utf-8")

app = replace_once(
    app,
    "    AudioInputDeviceSnapshot,\n    NormalizationAfterWorkerAction,\n",
    "    AudioInputDeviceSnapshot,\n"
    "    LatexMonitorCoordinator,\n"
    "    LatexMonitorScanTrigger,\n"
    "    NormalizationAfterWorkerAction,\n",
    "latex monitor application imports",
)
app = replace_once(
    app,
    "from .localization import select_subject, set_subject_combo, subject_value\n"
    "from .normalization import NormalizationReviewDialog\n",
    "from .localization import select_subject, set_subject_combo, subject_value\n"
    "from .latex_monitor_presentation import (\n"
    "    LatexMonitorPresentation,\n"
    "    build_latex_monitor_failure_presentation,\n"
    "    build_latex_monitor_no_update_presentation,\n"
    "    build_latex_monitor_result_presentation,\n"
    "    build_latex_monitor_scanning_presentation,\n"
    "    build_latex_monitor_toggle_presentation,\n"
    ")\n"
    "from .normalization import NormalizationReviewDialog\n",
    "latex monitor presentation imports",
)
app = replace_once(
    app,
    "        self.normalization_coordinator = NormalizationCoordinator()\n"
    "        self._normalization_cancellation: CancellationToken | None = None\n",
    "        self.normalization_coordinator = NormalizationCoordinator()\n"
    "        self.latex_monitor_coordinator = LatexMonitorCoordinator()\n"
    "        self._normalization_cancellation: CancellationToken | None = None\n",
    "latex monitor coordinator construction",
)
app = replace_once(
    app,
    "        self.latex_poll_timer.setInterval(self.config.latex.poll_seconds * 1000)\n"
    "        self.latex_poll_timer.timeout.connect(self.scan_remote_latex)\n",
    "        self.latex_poll_timer.setInterval(self.config.latex.poll_seconds * 1000)\n"
    "        self.latex_poll_timer.timeout.connect(\n"
    "            lambda: self.scan_remote_latex(periodic=True)\n"
    "        )\n",
    "latex monitor timer trigger",
)

monitor_block = '''    def _apply_latex_monitor_presentation(\n        self,\n        presentation: LatexMonitorPresentation,\n    ) -> None:\n        self.latex_monitor_status.setText(presentation.monitor_status)\n        if presentation.log_text is not None:\n            self.compilation_log.setPlainText(presentation.log_text)\n        if presentation.replace_previews:\n            self.pdf_previews.clear()\n            for path in presentation.preview_paths:\n                item = QListWidgetItem(path.name)\n                item.setData(256, str(path.resolve()))\n                self.pdf_previews.addItem(item)\n        self._set_status(presentation.app_status, presentation.tone)\n        if presentation.dialog_message is None:\n            return\n        if presentation.dialog_kind == "critical":\n            QMessageBox.critical(\n                self,\n                presentation.dialog_title or "Ошибка",\n                presentation.dialog_message,\n            )\n        else:\n            QMessageBox.information(\n                self,\n                presentation.dialog_title or "Готово",\n                presentation.dialog_message,\n            )\n\n    def toggle_latex_monitor(self, enabled: bool) -> None:\n        self.latex_monitor_coordinator.set_enabled(enabled)\n        if enabled:\n            self.latex_poll_timer.start()\n        else:\n            self.latex_poll_timer.stop()\n        self._apply_latex_monitor_presentation(\n            build_latex_monitor_toggle_presentation(\n                enabled=enabled,\n                poll_seconds=self.config.latex.poll_seconds,\n            )\n        )\n        if enabled:\n            self.scan_remote_latex(trigger=LatexMonitorScanTrigger.ENABLE)\n\n    def scan_remote_latex(\n        self,\n        _checked: bool = False,\n        *,\n        periodic: bool = False,\n        trigger: LatexMonitorScanTrigger | None = None,\n    ) -> None:\n        del _checked\n        selected_trigger = trigger or (\n            LatexMonitorScanTrigger.PERIODIC\n            if periodic\n            else LatexMonitorScanTrigger.MANUAL\n        )\n        decision = self.latex_monitor_coordinator.request_scan(selected_trigger)\n        if not decision.should_start:\n            return\n\n        from ..latex import RemoteLatexService\n\n        self._apply_latex_monitor_presentation(\n            build_latex_monitor_scanning_presentation()\n        )\n\n        def scan():\n            with self.content_service.activity("latex-monitor"):\n                service = RemoteLatexService(self.config.repository, self.config.latex)\n                for lesson in self.pipeline.store.list():\n                    if service.is_ready(lesson):\n                        return service.compile_lesson(\n                            lesson,\n                            cache_dir=self.pipeline.lesson_dir(lesson) / "latex-cache",\n                        )\n            return None\n\n        worker = Worker(scan)\n        worker.succeeded.connect(self._remote_compilation_ready)\n        worker.failed.connect(self._latex_monitor_failed)\n        worker.finished.connect(lambda: self._latex_monitor_worker_finished(worker))\n        self.workers.append(worker)\n        worker.start()\n\n    def _remote_compilation_ready(self, remote_result) -> None:\n        if remote_result is None:\n            self._apply_latex_monitor_presentation(\n                build_latex_monitor_no_update_presentation()\n            )\n            return\n        lesson = remote_result.lesson\n        self.pipeline.save_state(\n            lesson,\n            "latex",\n            "status",\n            "error",\n            force_status=True,\n        )\n        result = remote_result.compilation\n        self._apply_latex_monitor_presentation(\n            build_latex_monitor_result_presentation(\n                branch=remote_result.branch,\n                success=result.success,\n                attempt=lesson.latex.attempt,\n                max_attempts=self.config.latex.max_attempts,\n                errors=result.errors,\n                warnings=result.warnings,\n                preview_paths=result.preview_files,\n            )\n        )\n\n    def _latex_monitor_failed(self, details: str) -> None:\n        logging.error(details)\n        self._apply_latex_monitor_presentation(\n            build_latex_monitor_failure_presentation(details)\n        )\n\n    def _latex_monitor_worker_finished(self, worker: Worker) -> None:\n        self.latex_monitor_coordinator.finish_scan()\n        self._worker_finished(worker)\n\n'''
app = replace_between(
    app,
    "    def toggle_latex_monitor(self, enabled: bool) -> None:\n",
    "    def _operation_failed(self, purpose: str, details: str) -> None:\n",
    monitor_block,
    "latex monitor workflow",
)
app = replace_once(
    app,
    "        elif purpose == \"compile\":\n"
    "            self.compile_tex_button.setEnabled(True)\n"
    "        elif purpose == \"latex-monitor\":\n"
    "            self.latex_monitor_status.setText(\"Ошибка проверки удалённых TEX-файлов\")\n",
    "        elif purpose == \"compile\":\n"
    "            self.compile_tex_button.setEnabled(True)\n",
    "generic latex monitor failure branch",
)

APP.write_text(app, encoding="utf-8")
