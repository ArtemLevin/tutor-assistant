from __future__ import annotations

from pathlib import Path


APP = Path("src/tutor_assistant/ui/app.py")
CONCURRENT = Path("src/tutor_assistant/ui/concurrent_app.py")


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
app = replace_once(app, "import logging\nimport queue\nimport sys\n", "import logging\nimport sys\n", "remove stdlib queue")
app = replace_once(
    app,
    "    RecordingRuntimeRecorder,\n    SystemAudioSourceSnapshot,\n)",
    "    RecordingRuntimeRecorder,\n    SystemAudioSourceSnapshot,\n"
    "    TranscriptionAudioMissingError,\n"
    "    TranscriptionPumpContext,\n"
    "    TranscriptionQueueCoordinator,\n"
    ")",
    "application imports",
)
app = replace_once(
    app,
    "from ..transcript_editing import select_verified_text\n"
    "from ..transcription_queue import QueueStatus, TranscriptionQueue\n",
    "from ..transcript_editing import select_verified_text\n",
    "remove raw queue import",
)
app = replace_once(
    app,
    "from .transcript_workspace import (\n"
    "    NormalizationSettingsDialog,\n"
    "    TranscriptWorkspace,\n"
    ")\n",
    "from .transcript_workspace import (\n"
    "    NormalizationSettingsDialog,\n"
    "    TranscriptWorkspace,\n"
    ")\n"
    "from .transcription_queue_presentation import build_transcription_queue_presentation\n"
    "from .transcription_worker import TranscriptionWorker\n",
    "queue UI adapter imports",
)
app = replace_between(
    app,
    "class TranscriptionWorker(QThread):\n",
    "class MainWindow(QMainWindow):\n",
    "",
    "extract TranscriptionWorker",
)
app = replace_once(
    app,
    "        self.transcription_queue = TranscriptionQueue(self.pipeline.store)\n",
    "        self.transcription_queue_coordinator = TranscriptionQueueCoordinator(\n"
    "            self.pipeline.store,\n"
    "            retry_state_writer=self._persist_transcription_retry_state,\n"
    "        )\n",
    "coordinator construction",
)
app = replace_once(
    app,
    "            self.transcription_queue.discard(lesson_id)\n",
    "            self.transcription_queue_coordinator.discard(lesson_id)\n",
    "discard through coordinator",
)
restore_block = '''    def _persist_transcription_retry_state(self, lesson: Lesson) -> None:\n        self.pipeline.save_state(\n            lesson,\n            "status",\n            "error",\n            force_status=True,\n        )\n\n    def _restore_background_jobs(self) -> None:\n        restored = self.transcription_queue_coordinator.restore_history(\n            self.pipeline.store.list(limit=1000),\n            self.pipeline.store.list_transcription_jobs(),\n        )\n        if restored:\n            self._update_transcription_queue_ui()\n            self._pump_transcription_queue()\n            self._set_status(f"Восстановлена история обработки · {restored}", "working")\n\n'''
app = replace_between(
    app,
    "    def _restore_background_jobs(self) -> None:\n",
    "    def _load_lesson(self, lesson: Lesson) -> None:\n",
    restore_block,
    "restore orchestration",
)
queue_block = '''    def _enqueue_transcription(self, lesson: Lesson, audio: Path) -> None:\n        job = self.transcription_queue_coordinator.enqueue(lesson, audio)\n        logging.info("Транскрибация поставлена в очередь: lesson=%s audio=%s", job.id, audio)\n        self._update_transcription_queue_ui()\n        self._pump_transcription_queue()\n\n    def _pump_transcription_queue(self) -> None:\n        submission = self.transcription_queue_coordinator.pump(\n            TranscriptionPumpContext(\n                shutdown_requested=self._shutdown_requested,\n                normalization_active=self._normalization_cancellation is not None,\n            )\n        )\n        if submission is None:\n            return\n        self._update_transcription_queue_ui()\n        self.transcription_worker.submit(\n            submission.job_id,\n            submission.lesson,\n            submission.audio,\n        )\n\n    def _background_transcription_ready(self, job_id: str, lesson: Lesson) -> None:\n        self.transcription_queue_coordinator.complete(job_id, lesson)\n        if (\n            self.config.normalization.enabled\n            and self.config.normalization.auto_run\n            and lesson.lesson_id not in self._pending_auto_normalizations\n        ):\n            self._pending_auto_normalizations.append(lesson.lesson_id)\n        self._update_transcription_queue_ui()\n        self._set_status(f"Транскрипт готов · {lesson.student.full_name}", "warning")\n        logging.info("Фоновая транскрибация завершена: lesson=%s", lesson.lesson_id)\n        self._pump_transcription_queue()\n        QTimer.singleShot(0, self._pump_auto_normalization)\n\n    def _background_transcription_failed(self, job_id: str, details: str) -> None:\n        job = self.transcription_queue_coordinator.fail(job_id, details)\n        self._update_transcription_queue_ui()\n        logging.error("Фоновая транскрибация завершилась с ошибкой: lesson=%s\\n%s", job_id, details)\n        self._set_status(f"Ошибка транскрибации · {job.lesson.student.full_name}", "error")\n        self._pump_transcription_queue()\n\n    def _update_transcription_queue_ui(self) -> None:\n        if not hasattr(self, "processing_list"):\n            return\n        presentation = build_transcription_queue_presentation(\n            self.transcription_queue_coordinator.snapshot()\n        )\n        self.processing_list.clear()\n        for row in presentation.rows:\n            item = QListWidgetItem(row.text)\n            item.setData(256, row.job_id)\n            if row.tooltip:\n                item.setToolTip(row.tooltip)\n            self.processing_list.addItem(item)\n        self.processing_summary.setText(presentation.summary_text)\n        self.quick_queue_button.setText(presentation.badge_text)\n        self.quick_queue_button.setToolTip(presentation.badge_tooltip)\n\n    def _show_processing_queue(self) -> None:\n        self._set_mode("detailed")\n        self.tabs.setCurrentIndex(4)\n\n    def _sync_processing_actions(self) -> None:\n        if hasattr(self, "processing_open_button"):\n            self.processing_open_button.setEnabled(self.processing_list.currentItem() is not None)\n\n    def _open_selected_processing_item(self) -> None:\n        item = self.processing_list.currentItem()\n        if item is not None:\n            self._open_processing_item(item)\n\n    def _retry_transcription_job(self, job_id: str) -> bool:\n        try:\n            self.transcription_queue_coordinator.retry(job_id)\n        except TranscriptionAudioMissingError as exc:\n            QMessageBox.critical(self, "Ошибка", f"Аудиофайл не найден: {exc.path}")\n            return False\n        self._update_transcription_queue_ui()\n        self._pump_transcription_queue()\n        return True\n\n    def _open_processing_item(self, item: QListWidgetItem) -> None:\n        job = self.transcription_queue_coordinator.get(str(item.data(256)))\n        if job is None:\n            return\n        if (self.recorder and self.recorder.active) or self._recording_stop_started:\n            QMessageBox.warning(\n                self,\n                "Идёт запись",\n                "Завершите текущую запись перед открытием другого занятия.",\n            )\n            return\n        if job.status.value == "failed":\n            answer = QMessageBox.question(\n                self,\n                "Ошибка транскрибации",\n                f"{job.error or 'Неизвестная ошибка'}\\n\\nПовторить транскрибацию?",\n                QMessageBox.Yes | QMessageBox.No,\n                QMessageBox.Yes,\n            )\n            if answer == QMessageBox.Yes:\n                self._retry_transcription_job(job.id)\n            return\n        if job.status.value != "ready":\n            self._set_status("Транскрипт ещё обрабатывается", "working")\n            return\n        self._load_lesson(job.lesson)\n\n'''
app = replace_between(
    app,
    "    def _enqueue_transcription(self, lesson: Lesson, audio: Path) -> None:\n",
    "    def _load_segments(self, path: Path) -> None:\n",
    queue_block,
    "queue UI orchestration",
)

if "self.transcription_queue." in app:
    raise RuntimeError("raw transcription_queue mutation remains in app.py")
if "QueueStatus" in app:
    raise RuntimeError("QueueStatus remains in app.py")
if "class TranscriptionWorker" in app:
    raise RuntimeError("TranscriptionWorker remains in app.py")
APP.write_text(app, encoding="utf-8")

concurrent = CONCURRENT.read_text(encoding="utf-8")
concurrent_block = '''    def _open_processing_item(self, item: QListWidgetItem) -> None:\n        job = self.transcription_queue_coordinator.get(str(item.data(256)))\n        if job is None:\n            return\n\n        action = processing_action(job.status.value)\n        if action == ProcessingAction.RETRY:\n            answer = QMessageBox.question(\n                self,\n                "Ошибка транскрибации",\n                (job.error or "Неизвестная ошибка") + "\\n\\nПовторить транскрибацию?",\n                QMessageBox.Yes | QMessageBox.No,\n                QMessageBox.Yes,\n            )\n            if answer == QMessageBox.Yes:\n                self._retry_transcription_job(job.id)\n            return\n        if action == ProcessingAction.WAIT:\n            self._set_status("Транскрипт ещё обрабатывается", "working")\n            return\n\n        # A ready transcript is text-only and is safe to open during another recording.\n        self._load_review_lesson(job.lesson, restore_form=False)\n\n'''
concurrent = replace_between(
    concurrent,
    "    def _open_processing_item(self, item: QListWidgetItem) -> None:\n",
    "    def play_selected_segment(self, _index=None) -> None:\n",
    concurrent_block,
    "concurrent processing action",
)
if "self.transcription_queue." in concurrent:
    raise RuntimeError("raw transcription_queue mutation remains in concurrent_app.py")
CONCURRENT.write_text(concurrent, encoding="utf-8")

# Separate trigger commit: the workflow is already present on the branch.
