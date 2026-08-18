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
    "    AudioInputDeviceSnapshot,\n    RecordingHealthAction,\n",
    "    AudioInputDeviceSnapshot,\n"
    "    NormalizationAfterWorkerAction,\n"
    "    NormalizationAutoAction,\n"
    "    NormalizationAutoContext,\n"
    "    NormalizationCoordinator,\n"
    "    NormalizationManualStartContext,\n"
    "    NormalizationStartBlock,\n"
    "    RecordingHealthAction,\n",
    "application normalization imports",
)
app = replace_once(
    app,
    "from .normalization import NormalizationReviewDialog\n"
    "from .normalization_provider import (\n",
    "from .normalization import NormalizationReviewDialog\n"
    "from .normalization_presentation import (\n"
    "    NormalizationControlContext,\n"
    "    build_normalization_controls,\n"
    "    build_normalization_failure_presentation,\n"
    "    build_normalization_ready_presentation,\n"
    ")\n"
    "from .normalization_provider import (\n",
    "normalization presentation imports",
)
app = replace_once(
    app,
    "        self._normalization_cancellation: CancellationToken | None = None\n"
    "        self._normalization_execution: NormalizationExecution | None = None\n"
    "        self._normalization_lesson_id: str | None = None\n"
    "        self._retry_indeterminate_after_worker = False\n"
    "        self._cloud_consent_session = CloudConsentSession()\n"
    "        self._pending_auto_normalizations: list[str] = []\n",
    "        self.normalization_coordinator = NormalizationCoordinator()\n"
    "        self._normalization_cancellation: CancellationToken | None = None\n"
    "        self._normalization_execution: NormalizationExecution | None = None\n"
    "        self._normalization_lesson_id: str | None = None\n"
    "        self._cloud_consent_session = CloudConsentSession()\n",
    "normalization coordinator construction",
)
app = replace_once(
    app,
    "        if (\n"
    "            self.config.normalization.enabled\n"
    "            and self.config.normalization.auto_run\n"
    "            and lesson.lesson_id not in self._pending_auto_normalizations\n"
    "        ):\n"
    "            self._pending_auto_normalizations.append(lesson.lesson_id)\n",
    "        if self.config.normalization.enabled and self.config.normalization.auto_run:\n"
    "            self.normalization_coordinator.enqueue_auto(lesson.lesson_id)\n",
    "auto normalization enqueue",
)
app = replace_once(
    app,
    "    def _show_normalization_settings(self) -> None:\n"
    "        if self._normalization_cancellation is not None:\n",
    "    def _show_normalization_settings(self) -> None:\n"
    "        if self.normalization_coordinator.active:\n",
    "settings active guard",
)
app = replace_once(
    app,
    "        if self._normalization_cancellation is not None:\n"
    "            self._set_normalization_provider_combo(current)\n",
    "        if self.normalization_coordinator.active:\n"
    "            self._set_normalization_provider_combo(current)\n",
    "provider active guard",
)

sync_block = '''    def _sync_normalization_controls(self) -> None:\n        if not hasattr(self, "transcript_workspace"):\n            return\n        self._sync_transcript_workspace_context()\n        self.transcript_workspace.set_config_summary(\n            self._normalization_settings_summary()\n        )\n\n        run = (\n            self.normalization_service.runs.latest(self.lesson.lesson_id)\n            if self.lesson\n            else None\n        )\n        provider_error = provider_configuration_error(self.config.normalization)\n        artifact_ready = bool(\n            run\n            and run.artifact_path\n            and (self.content_service.workspace / run.artifact_path).is_file()\n        )\n        preview = self._normalization_preview(run)\n        presentation = build_normalization_controls(\n            NormalizationControlContext(\n                lifecycle_state=self.normalization_coordinator.state,\n                has_lesson=self.lesson is not None,\n                enabled=self.config.normalization.enabled,\n                has_segments=bool(self.segment_table.rowCount()),\n                provider_error=provider_error,\n                run_status=run.status if run else None,\n                artifact_ready=artifact_ready,\n                review_candidate_chunks=(\n                    preview.statistics.review_candidate_chunks if preview else 0\n                ),\n                fallback_chunks=(\n                    preview.statistics.source_fallback_chunks if preview else 0\n                ),\n                warning_count=len(preview.quality.warnings) if preview else 0,\n                progress=self.normalization_coordinator.progress,\n            )\n        )\n\n        self.normalization_provider.setEnabled(presentation.provider_enabled)\n        self.transcript_workspace.settings_button.setEnabled(\n            presentation.settings_enabled\n        )\n        self.transcript_workspace.set_review_action(\n            visible=presentation.review_visible,\n            enabled=presentation.review_enabled,\n            text=presentation.review_text,\n        )\n        self.transcript_workspace.set_menu_state(\n            restart=presentation.menu.restart,\n            open_artifact=presentation.menu.open_artifact,\n            show_warnings=presentation.menu.show_warnings,\n            reject=presentation.menu.reject,\n        )\n        self._transcript_primary_action = presentation.primary.action\n        primary_kwargs = {\n            "enabled": presentation.primary.enabled,\n            "visible": presentation.primary.visible,\n        }\n        if presentation.primary.kind is not None:\n            primary_kwargs["kind"] = presentation.primary.kind\n        self.transcript_workspace.set_primary_action(\n            presentation.primary.text,\n            **primary_kwargs,\n        )\n        self.transcript_workspace.set_process_state(\n            presentation.process.title,\n            presentation.process.detail,\n            tone=presentation.process.tone,\n            show_progress=presentation.process.show_progress,\n        )\n\n'''
app = replace_between(
    app,
    "    def _sync_normalization_controls(self) -> None:\n",
    "    def normalize_current_transcript(\n",
    sync_block,
    "normalization controls",
)

manual_block = '''    def normalize_current_transcript(\n        self,\n        _checked: bool = False,\n        *,\n        force: bool = False,\n        retry_indeterminate: bool = False,\n    ) -> None:\n        del _checked\n        provider = self._selected_normalization_provider()\n        decision = self.normalization_coordinator.evaluate_manual_start(\n            NormalizationManualStartContext(\n                lesson_id=self.lesson.lesson_id if self.lesson else None,\n                provider=provider,\n                provider_error=provider_configuration_error(self.config.normalization),\n                has_segments=bool(self.segment_table.rowCount()),\n                transcription_busy=(\n                    self.transcription_worker.busy\n                    or self.transcription_queue_coordinator.active is not None\n                ),\n            )\n        )\n        if not decision.allowed:\n            if decision.block == NormalizationStartBlock.NO_LESSON:\n                QMessageBox.warning(\n                    self,\n                    "LLM-фильтрация",\n                    "Сначала откройте транскрипт занятия",\n                )\n            elif decision.block == NormalizationStartBlock.PROVIDER_ERROR:\n                QMessageBox.warning(\n                    self,\n                    "LLM-фильтрация",\n                    decision.detail or "Провайдер LLM не настроен",\n                )\n            elif decision.block == NormalizationStartBlock.TRANSCRIPTION_BUSY:\n                QMessageBox.warning(\n                    self,\n                    "LLM-фильтрация",\n                    "Дождитесь завершения активной Whisper-транскрибации: оба процесса используют CPU.",\n                )\n            elif decision.block == NormalizationStartBlock.ALREADY_RUNNING:\n                self._set_status("LLM-фильтрация уже выполняется", "warning")\n            elif decision.block == NormalizationStartBlock.NO_SEGMENTS:\n                QMessageBox.warning(\n                    self,\n                    "LLM-фильтрация",\n                    "В транскрипте нет сегментов",\n                )\n            return\n\n        lesson = self.lesson\n        assert lesson is not None\n        segments = self._current_source_segments()\n        lesson_id = lesson.lesson_id\n        try:\n            model = self._persist_selected_normalization_model()\n        except Exception as exc:\n            QMessageBox.warning(self, "LLM-фильтрация", str(exc))\n            return\n        cloud_consent = None\n        if provider == "yandex_ai_studio":\n            try:\n                cloud_consent = self._request_cloud_consent(\n                    lesson_id,\n                    model,\n                    segments,\n                )\n            except Exception as exc:\n                QMessageBox.warning(self, "Облачная обработка", str(exc))\n                return\n            if cloud_consent is None:\n                self._set_status("Облачная обработка отменена", "warning")\n                return\n\n        self.normalization_coordinator.begin(lesson_id)\n        token = CancellationToken()\n        self._normalization_cancellation = token\n        self._normalization_lesson_id = lesson_id\n        self._sync_normalization_controls()\n        self._set_status(\n            f"Фильтрую учебное содержание · {provider_label(provider)} · {model}",\n            "working",\n        )\n        self._launch_normalization_worker(\n            lesson_id,\n            token,\n            model=model,\n            force=force,\n            source_segments=segments,\n            source_artifact="review-buffer",\n            retry_indeterminate=retry_indeterminate,\n            cloud_consent=cloud_consent,\n        )\n\n    def _launch_normalization_worker(\n        self,\n        lesson_id: str,\n        token: CancellationToken,\n        **kwargs,\n    ) -> None:\n        worker = NormalizationWorker(\n            self.normalization_service,\n            lesson_id=lesson_id,\n            cancellation=token,\n            **kwargs,\n        )\n        worker.progress.connect(self._normalization_progress_updated)\n        worker.resume_confirmation_required.connect(\n            self._normalization_resume_confirmation_required\n        )\n        worker.succeeded.connect(\n            lambda result, expected=lesson_id: self._normalization_ready(\n                result,\n                expected,\n            )\n        )\n        worker.failed.connect(self._normalization_failed)\n        worker.finished.connect(lambda: self._normalization_worker_finished(worker))\n        self.workers.append(worker)\n        worker.start()\n\n'''
app = replace_between(
    app,
    "    def normalize_current_transcript(\n",
    "    def _pump_auto_normalization(self) -> None:\n",
    manual_block,
    "manual normalization orchestration",
)

auto_block = '''    def _pump_auto_normalization(self) -> None:\n        decision = self.normalization_coordinator.pump_auto(\n            NormalizationAutoContext(\n                provider=self.config.normalization.provider,\n                shutdown_requested=self._shutdown_requested,\n                transcription_busy=(\n                    self.transcription_worker.busy\n                    or self.transcription_queue_coordinator.active is not None\n                ),\n            )\n        )\n        if decision.action == NormalizationAutoAction.WAITING_CLOUD_CONSENT:\n            self._set_status(\n                "Облачная автофильтрация ожидает ручного согласия преподавателя",\n                "warning",\n            )\n            return\n        if decision.action != NormalizationAutoAction.START:\n            return\n        lesson_id = decision.lesson_id\n        assert lesson_id is not None\n        token = CancellationToken()\n        self._normalization_cancellation = token\n        self._normalization_lesson_id = lesson_id\n        self._sync_normalization_controls()\n        self._launch_normalization_worker(lesson_id, token)\n\n'''
app = replace_between(
    app,
    "    def _pump_auto_normalization(self) -> None:\n",
    "    def _normalization_progress_updated(self, progress) -> None:\n",
    auto_block,
    "auto normalization orchestration",
)

progress_block = '''    def _normalization_progress_updated(self, progress) -> None:\n        self.normalization_coordinator.update_progress(progress)\n        self._sync_normalization_controls()\n\n'''
app = replace_between(
    app,
    "    def _normalization_progress_updated(self, progress) -> None:\n",
    "    def _normalization_resume_confirmation_required(self, error) -> None:\n",
    progress_block,
    "normalization progress presentation",
)

resume_block = '''    def _normalization_resume_confirmation_required(self, error) -> None:\n        answer = QMessageBox.question(\n            self,\n            "Повторный облачный запрос",\n            str(error) + "\\n\\nПовторить только неопределённые блоки?",\n            QMessageBox.Yes | QMessageBox.No,\n            QMessageBox.No,\n        )\n        self.normalization_coordinator.record_resume_confirmation(\n            answer == QMessageBox.Yes\n        )\n        self._set_status(\n            "Требуется подтверждение повторного облачного запроса",\n            "warning",\n        )\n\n'''
app = replace_between(
    app,
    "    def _normalization_resume_confirmation_required(self, error) -> None:\n",
    "    def cancel_normalization(self) -> None:\n",
    resume_block,
    "normalization resume decision",
)

cancel_block = '''    def cancel_normalization(self) -> None:\n        if self._normalization_cancellation is None:\n            return\n        if not self.normalization_coordinator.request_cancel():\n            return\n        self._normalization_cancellation.cancel()\n        self._sync_normalization_controls()\n        self._set_status("Отмена нормализации запрошена…", "warning")\n\n'''
app = replace_between(
    app,
    "    def cancel_normalization(self) -> None:\n",
    "    def _normalization_ready(\n",
    cancel_block,
    "normalization cancellation",
)

ready_block = '''    def _normalization_ready(\n        self,\n        result: NormalizationExecution,\n        expected_lesson_id: str,\n    ) -> None:\n        presentation = build_normalization_ready_presentation(result)\n        if (\n            self.lesson\n            and self.lesson.lesson_id == expected_lesson_id\n            and result.transcript.lesson_id == expected_lesson_id\n        ):\n            self._normalization_execution = result\n            self.transcript_workspace.set_result_preview(\n                result.transcript.educational_text,\n                summary=presentation.preview_summary,\n                warnings=result.transcript.quality.warnings,\n                select=True,\n            )\n        self.transcript_workspace.set_process_state(\n            presentation.process_title,\n            presentation.process_detail,\n            tone=presentation.process_tone,\n        )\n        self._set_status(\n            presentation.status_text,\n            presentation.status_tone,\n        )\n        logging.info(\n            "event=normalization_gui_ready lesson_id=%s run_id=%s",\n            expected_lesson_id,\n            result.run.id if result.run else "dry-run",\n        )\n\n'''
app = replace_between(
    app,
    "    def _normalization_ready(\n",
    "    def _normalization_failed(self, details: str) -> None:\n",
    ready_block,
    "normalization result presentation",
)

failed_block = '''    def _normalization_failed(self, details: str) -> None:\n        logging.error("LLM-фильтрация завершилась ошибкой:\\n%s", details)\n        presentation = build_normalization_failure_presentation(details)\n        self._transcript_primary_action = "retry"\n        self.transcript_workspace.set_primary_action(\n            "Повторить",\n            enabled=True,\n        )\n        self.transcript_workspace.set_process_state(\n            presentation.process_title,\n            presentation.message,\n            tone=presentation.tone,\n        )\n        self._set_status(presentation.status_text, presentation.tone)\n        QMessageBox.warning(self, "LLM-фильтрация", presentation.message)\n\n'''
app = replace_between(
    app,
    "    def _normalization_failed(self, details: str) -> None:\n",
    "    def _normalization_worker_finished(self, worker: Worker) -> None:\n",
    failed_block,
    "normalization failure presentation",
)

finished_block = '''    def _normalization_worker_finished(self, worker: Worker) -> None:\n        next_action = self.normalization_coordinator.finish_worker()\n        self._normalization_cancellation = None\n        self._normalization_lesson_id = None\n        self._worker_finished(worker)\n        self._sync_normalization_controls()\n        self._pump_transcription_queue()\n        if next_action == NormalizationAfterWorkerAction.RETRY_INDETERMINATE:\n            QTimer.singleShot(\n                0,\n                lambda: self.normalize_current_transcript(retry_indeterminate=True),\n            )\n        else:\n            QTimer.singleShot(0, self._pump_auto_normalization)\n\n'''
app = replace_between(
    app,
    "    def _normalization_worker_finished(self, worker: Worker) -> None:\n",
    "    def _normalization_payload(\n",
    finished_block,
    "normalization worker completion",
)

if "_pending_auto_normalizations" in app:
    raise RuntimeError("legacy pending auto normalization list remains")
if "_retry_indeterminate_after_worker" in app:
    raise RuntimeError("legacy retry-indeterminate flag remains")
if "process_detail.text()" in app:
    raise RuntimeError("normalization presentation still reads widget text as state")
if "normalization_coordinator = NormalizationCoordinator()" not in app:
    raise RuntimeError("normalization coordinator not wired")
if "build_normalization_controls(" not in app:
    raise RuntimeError("normalization presentation boundary not wired")

APP.write_text(app, encoding="utf-8")
