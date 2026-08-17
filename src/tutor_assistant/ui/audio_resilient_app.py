from __future__ import annotations

import logging

from PySide6.QtWidgets import QMessageBox

from ..application.recording import (
    RecordingRuntimeState,
    RecordingWorkflowController,
    RecordingWorkflowRejected,
)
from ..domain import JobStatus
from ..recording import SystemAudioSource, list_input_devices, list_system_audio_sources
from ..recording.devices import probe_input_device, resolve_input_device
from . import app as base_app
from .transcript_publication_app import MainWindow as ProductionMainWindow


class MainWindow(ProductionMainWindow):
    """Production window with hot-plug-safe audio and application recording lifecycle."""

    def __init__(self, config_path):
        self.recording_workflow = RecordingWorkflowController()
        super().__init__(config_path)
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
        super()._begin_preflight(show_intro)

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

        try:
            super().start_recording()
        finally:
            self.recording_workflow.observe_runtime(self._recording_runtime_state())

    def _stop_recording_async(self, reason: str | None = None) -> None:
        if not self.recording_workflow.begin_stop(self._recording_runtime_state()):
            return
        try:
            super()._stop_recording_async(reason)
        finally:
            self.recording_workflow.observe_runtime(self._recording_runtime_state())

    def _recording_ready(
        self,
        result,
        recorded_lesson,
        source_recorder,
        reason: str | None = None,
    ) -> None:
        try:
            super()._recording_ready(result, recorded_lesson, source_recorder, reason)
        finally:
            if recorded_lesson.status == JobStatus.RECORDED:
                self.recording_workflow.mark_completed()
            elif recorded_lesson.status == JobStatus.FAILED:
                self.recording_workflow.mark_failed()
            else:
                self.recording_workflow.observe_runtime(self._recording_runtime_state())

    def _recording_stop_failed(self, details: str) -> None:
        try:
            super()._recording_stop_failed(details)
        finally:
            self.recording_workflow.mark_recovery_required()

    def _recovery_ready(self, result) -> None:
        super()._recovery_ready(result)
        if not (self.recorder and self.recorder.active):
            self.recording_workflow.reset()


def main() -> None:
    base_app.MainWindow = MainWindow
    base_app.main()


if __name__ == "__main__":
    main()
