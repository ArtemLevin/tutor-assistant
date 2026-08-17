from __future__ import annotations

import logging

from PySide6.QtWidgets import QMessageBox

from ..application import AudioPreflightResult, AudioPreflightUseCase
from ..application.recording import (
    RecordingRuntimeState,
    RecordingWorkflowController,
    RecordingWorkflowRejected,
    StartRecordingUseCase,
)
from ..domain import Lesson
from ..quick_start import selected_profile
from ..recording import (
    DualRecorder,
    SystemAudioSource,
    list_input_devices,
    list_system_audio_sources,
)
from ..recording.devices import probe_input_device, resolve_input_device
from . import app as base_app
from .theme import refresh_style
from .transcript_publication_app import MainWindow as ProductionMainWindow


class MainWindow(ProductionMainWindow):
    """Production window with hot-plug-safe audio and application recording lifecycle."""

    def __init__(self, config_path):
        self.recording_workflow = RecordingWorkflowController()
        super().__init__(config_path)
        self.start_recording_use_case = StartRecordingUseCase(
            self.pipeline,
            self.content_service,
            self._create_live_recorder,
        )
        self.audio_preflight_use_case = AudioPreflightUseCase(
            self._create_preflight_recorder,
        )
        self.recording_workflow.observe_runtime(self._recording_runtime_state())
        try:
            self._refresh_live_audio_devices(require_ready=False)
        except Exception:
            logging.exception("Не удалось обновить аудиоустройства после запуска")

    def _recording_runtime_state(self) -> RecordingRuntimeState:
        return RecordingRuntimeState(
            active=bool(self.recorder and self.recorder.active),
            stopping=bool(self._recording_stop_started),
            shutdown_requested=bool(self._shutdown_requested),
        )

    def _create_live_recorder(self):
        return self._create_configured_recorder(
            self.config.recording.sample_rate,
            self.config.recording.channels,
            self.config.recording.chunk_seconds,
            self.config.recording.queue_blocks,
            self.config.recording.target_sample_rate,
        )

    def _create_preflight_recorder(self, chunk_seconds: int):
        return DualRecorder(
            self.config.recording.sample_rate,
            self.config.recording.channels,
            chunk_seconds,
            self.config.recording.queue_blocks,
            self.config.recording.target_sample_rate,
        )

    def _present_recording_started(
        self,
        recording_lesson: Lesson,
        system_source: SystemAudioSource,
    ) -> None:
        self._update_scheduled_occurrence(
            "in_progress",
            lesson_id=recording_lesson.lesson_id,
        )
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
            "Запись начата через application use case: lesson=%s mic=%s system=%s",
            recording_lesson.lesson_id,
            self.mic.currentText(),
            system_source.display_name,
        )

    def _reset_failed_start_presentation(self) -> None:
        self.recorder = None
        self.recording_lesson = None
        self._recording_lease = None
        self._update_scheduled_occurrence("planned", clear=True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.test_devices_button.setEnabled(True)
        self._quick_auto_transcribe_active = False
        self._refresh_quick_readiness()

    def _persist_audio_selection(self) -> None:
        selected_index = self.mic.currentData()
        selected = next(
            (device for device in self.devices if device.index == selected_index),
            None,
        )
        if selected is not None:
            self.config.recording.mic_device_name = selected.name
            self.config.recording.mic_host_api = selected.host_api
        super()._persist_audio_selection()

    def _refresh_live_audio_devices(
        self,
        *,
        require_ready: bool,
        probe: bool = False,
    ) -> None:
        previous_identity = (
            self.config.recording.mic_device,
            self.config.recording.mic_device_name,
            self.config.recording.mic_host_api,
            self.config.recording.system_device_id,
            self.config.recording.system_backend,
        )
        current_system = self.loopback.currentData() if hasattr(self, "loopback") else None
        devices = list_input_devices()
        sources = list_system_audio_sources(
            devices,
            self.config.recording.target_sample_rate,
        )
        microphone = resolve_input_device(
            devices,
            device_index=self.config.recording.mic_device,
            device_name=self.config.recording.mic_device_name,
            host_api=self.config.recording.mic_host_api,
        )

        wanted_system_id = self.config.recording.system_device_id
        wanted_system_backend = self.config.recording.system_backend
        if isinstance(current_system, SystemAudioSource):
            wanted_system_id = current_system.device_id
            wanted_system_backend = current_system.backend

        system_source = next(
            (
                source
                for source in sources
                if source.device_id == wanted_system_id
                and source.backend == wanted_system_backend
            ),
            None,
        )
        if (
            system_source is None
            and wanted_system_id is None
            and self.config.recording.loopback_device is not None
        ):
            system_source = next(
                (
                    source
                    for source in sources
                    if source.legacy_index == self.config.recording.loopback_device
                ),
                None,
            )
        if system_source is None and wanted_system_id is None and sources:
            system_source = sources[0]

        self.devices = devices
        self.system_sources = sources
        self._rebuild_microphone_combo(microphone)
        self._rebuild_system_combo(system_source)

        if microphone is not None:
            self.config.recording.mic_device = microphone.index
            self.config.recording.mic_device_name = microphone.name
            self.config.recording.mic_host_api = microphone.host_api
        if system_source is not None:
            self.config.recording.system_device_id = system_source.device_id
            self.config.recording.system_backend = system_source.backend
            self.config.recording.loopback_device = system_source.legacy_index

        current_identity = (
            self.config.recording.mic_device,
            self.config.recording.mic_device_name,
            self.config.recording.mic_host_api,
            self.config.recording.system_device_id,
            self.config.recording.system_backend,
        )
        if current_identity != previous_identity:
            self.preflight_passed = False
            self.preflight_result = None
            self.config.save(self.config_path)
            logging.info(
                "Аудиоустройства переопределены: mic=%s system=%s",
                self.mic.currentText(),
                self.loopback.currentText(),
            )

        if require_ready and microphone is None:
            raise RuntimeError(
                "Сохранённый микрофон больше не найден в Windows. "
                "Откройте рабочее пространство, выберите микрофон заново "
                "и повторите проверку аудио."
            )
        if require_ready and system_source is None:
            raise RuntimeError(
                "Сохранённый источник системного звука больше не найден. "
                "Выберите WASAPI Loopback-устройство заново и повторите проверку аудио."
            )
        if probe and microphone is not None:
            probe_input_device(microphone, channels=self.config.recording.channels)

    def _rebuild_microphone_combo(self, selected) -> None:
        self.mic.blockSignals(True)
        try:
            self.mic.clear()
            for device in self.devices:
                label = f"{device.index}: {device.name} [{device.host_api}]"
                self.mic.addItem(label, device.index)
            if selected is None:
                self.mic.setCurrentIndex(-1)
            else:
                index = self.mic.findData(selected.index)
                self.mic.setCurrentIndex(index)
        finally:
            self.mic.blockSignals(False)

    def _rebuild_system_combo(self, selected: SystemAudioSource | None) -> None:
        self.loopback.blockSignals(True)
        try:
            self.loopback.clear()
            for source in self.system_sources:
                self.loopback.addItem(source.display_name, source)
            if not self.system_sources:
                self.loopback.addItem("WASAPI Loopback-устройства не найдены", None)
                self.loopback.setEnabled(False)
                return
            self.loopback.setEnabled(True)
            if selected is None:
                self.loopback.setCurrentIndex(-1)
                return
            index = next(
                (
                    item
                    for item in range(self.loopback.count())
                    if isinstance(self.loopback.itemData(item), SystemAudioSource)
                    and self.loopback.itemData(item).key == selected.key
                ),
                -1,
            )
            self.loopback.setCurrentIndex(index)
        finally:
            self.loopback.blockSignals(False)

    def _prepare_audio_or_warn(self, *, probe: bool) -> bool:
        try:
            self._refresh_live_audio_devices(require_ready=True, probe=probe)
        except Exception as exc:
            logging.exception("Аудиоустройства недоступны перед запуском")
            self.preflight_passed = False
            self.preflight_result = None
            self._refresh_quick_readiness()
            QMessageBox.warning(self, "Проверка аудио", str(exc))
            return False
        return True

    def _begin_preflight(self, show_intro: bool) -> None:
        if not self._prepare_audio_or_warn(probe=True):
            self.test_devices_button.setEnabled(True)
            self._quick_start_pending = False
            self._quick_auto_transcribe_active = False
            self._refresh_quick_readiness()
            return

        mic_device = self.mic.currentData()
        system_source = self.loopback.currentData()
        if mic_device is None or not isinstance(system_source, SystemAudioSource):
            self.test_devices_button.setEnabled(True)
            self._quick_start_pending = False
            self._quick_auto_transcribe_active = False
            self._refresh_quick_readiness()
            QMessageBox.warning(
                self,
                "Проверка аудио",
                "Выберите микрофон и источник WASAPI Loopback для системного звука.",
            )
            return

        if show_intro:
            QMessageBox.information(
                self,
                "Тестовая запись",
                "После закрытия окна говорите в микрофон и одновременно воспроизводите звук. "
                f"Запись продлится {self.config.recording.diagnostics_seconds} секунд.",
            )

        self.test_devices_button.setEnabled(False)
        self._set_status("Записываю тест микрофона и системного звука…", "working")
        logging.info(
            "Тестовая запись запущена через application use case: mic=%s system=%s",
            self.mic.currentText(),
            system_source.display_name,
        )
        worker = base_app.Worker(
            self.audio_preflight_use_case.run,
            self.config.workspace,
            int(mic_device),
            system_source,
            self.config.recording.diagnostics_seconds,
            self.config.recording.chunk_seconds,
        )
        worker.succeeded.connect(self._device_test_ready)
        worker.failed.connect(lambda details: self._operation_failed("device-test", details))
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _device_test_ready(self, result: AudioPreflightResult) -> None:
        self.mic_level.setValue(round(min(1.0, result.microphone_rms * 5) * 100))
        self.system_level.setValue(round(min(1.0, result.system_rms * 5) * 100))
        warnings = list(result.warnings)
        self.preflight_passed = result.ready
        self.preflight_result = result
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
            "Тестовая запись завершена через application use case: ready=%s report=%s",
            self.preflight_passed,
            result.quality_report,
        )
        if self._quick_start_pending and self.preflight_passed:
            profile = selected_profile(self.config, self.quick_profile.currentData())
            self._start_quick_countdown(profile.countdown_seconds)
        elif self._quick_start_pending:
            self._quick_start_pending = False
            self._quick_auto_transcribe_active = False
            self._refresh_quick_readiness()

    def start_recording(self) -> None:
        try:
            self.recording_workflow.begin_start(self._recording_runtime_state())
        except RecordingWorkflowRejected as exc:
            logging.warning("Команда начала записи отклонена: %s", exc)
            QMessageBox.critical(self, "Ошибка записи", str(exc))
            return

        if not self._prepare_audio_or_warn(probe=True):
            self.recording_workflow.abort_start()
            self._quick_auto_transcribe_active = False
            self._update_scheduled_occurrence("planned", clear=True)
            return

        self.audio_output_format.setEnabled(False)
        started = None
        try:
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
                    self.recording_workflow.abort_start()
                    return

            system_source = self.loopback.currentData()
            if not isinstance(system_source, SystemAudioSource):
                raise ValueError("Выберите устройство WASAPI Loopback для системного звука")
            recording_lesson = self._build_lesson_from_form()
            started = self.start_recording_use_case.start(
                recording_lesson,
                mic_device=int(self.mic.currentData()),
                system_source=system_source,
            )
            self.recording_lesson = started.lesson
            self.recorder = started.recorder
            self._recording_lease = started.lease
            self._present_recording_started(recording_lesson, system_source)
        except Exception as exc:
            logging.exception("Не удалось начать запись через application use case")
            if started is not None:
                self.start_recording_use_case.abort(started, exc)
            self._reset_failed_start_presentation()
            self.recording_workflow.mark_failed()
            QMessageBox.critical(self, "Ошибка записи", str(exc))
        finally:
            self.recording_workflow.observe_runtime(self._recording_runtime_state())
            if not (self.recorder and self.recorder.active):
                self.audio_output_format.setEnabled(True)
            self._refresh_teacher_cockpit()
            self._sync_parallel_review_ui()


def main() -> None:
    base_app.MainWindow = MainWindow
    base_app.main()


if __name__ == "__main__":
    main()
