from __future__ import annotations

from pathlib import Path


APP = Path("src/tutor_assistant/ui/app.py")
AUDIO = Path("src/tutor_assistant/ui/audio_resilient_app.py")
WORKFLOW = Path(".github/workflows/agent_slice10_health.yml")
SCRIPT = Path(__file__)


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


app = APP.read_text(encoding="utf-8")
app = replace_once(
    app,
    """from ..application import (\n    AudioInputDeviceSnapshot,\n    RecordingRuntimeRecorder,\n    SystemAudioSourceSnapshot,\n)\n""",
    """from ..application import (\n    AudioInputDeviceSnapshot,\n    RecordingHealthAction,\n    RecordingHealthMonitor,\n    RecordingHealthPolicy,\n    RecordingHealthSample,\n    RecordingRuntimeRecorder,\n    SystemAudioSourceSnapshot,\n)\n""",
    label="application imports",
)
app = replace_once(
    app,
    """        self.recorder: RecordingRuntimeRecorder | None = None\n        self._recording_lease = None\n        self.preflight_passed = False\n""",
    """        self.recorder: RecordingRuntimeRecorder | None = None\n        self.recording_health_monitor = RecordingHealthMonitor(\n            RecordingHealthPolicy(\n                device_timeout_seconds=self.config.recording.device_timeout_seconds,\n                silence_warning_seconds=self.config.recording.silence_warning_seconds,\n            )\n        )\n        self._recording_lease = None\n        self.preflight_passed = False\n""",
    label="health monitor initialization",
)
app = replace_once(
    app,
    """        self._recording_stop_started = False\n        self._active_audio_warning = \"\"\n        self._quick_start_pending = False\n""",
    """        self._recording_stop_started = False\n        self._quick_start_pending = False\n""",
    label="legacy active warning state",
)
old_tick = '''    def _tick(self) -> None:\n        self.recording_seconds += 1\n        hours, remainder = divmod(self.recording_seconds, 3600)\n        minutes, seconds = divmod(remainder, 60)\n        self.duration.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")\n        if self.recorder and self.recorder.active:\n            levels = self.recorder.levels\n            health = self.recorder.health\n            self.mic_level.setValue(round(levels.microphone * 100))\n            self.system_level.setValue(round(levels.system * 100))\n            dropped = health.microphone_dropped_blocks + health.system_dropped_blocks\n            self.recording_health_label.setText(\n                f"Очереди: {health.microphone_queue_percent}% / {health.system_queue_percent}%; "\n                f"потеряно блоков: {dropped}; задержка writer: {health.max_writer_latency_ms:.1f} мс; "\n                f"тишина: {health.microphone_silence_seconds:.0f} / "\n                f"{health.system_silence_seconds:.0f} с; переподключения: {health.reconnect_attempts}"\n            )\n            timeout = self.config.recording.device_timeout_seconds\n            if health.stream_errors:\n                self._stop_recording_async("Ошибка аудиоустройства: " + "; ".join(health.stream_errors))\n                return\n            if self.recording_seconds > timeout and (\n                health.microphone_callback_age_seconds > timeout\n                or health.system_callback_age_seconds > timeout\n            ):\n                self._stop_recording_async("Потерян поток аудиоустройства; сохранены доступные чанки записи")\n                return\n            silence_limit = self.config.recording.silence_warning_seconds\n            warnings = []\n            if health.microphone_silence_seconds >= silence_limit:\n                warnings.append(f"микрофон молчит {health.microphone_silence_seconds:.0f} с")\n            if health.system_silence_seconds >= silence_limit:\n                warnings.append(f"звук ученика отсутствует {health.system_silence_seconds:.0f} с")\n            if dropped:\n                warnings.append(f"потеряно блоков: {dropped}")\n            warning = "; ".join(warnings)\n            if warning and warning != self._active_audio_warning:\n                self._active_audio_warning = warning\n                self._set_status("Проверьте аудио · " + warning, "warning")\n                logging.warning("Контроль записи: %s", warning)\n            elif not warning and self._active_audio_warning:\n                self._active_audio_warning = ""\n                self._set_status("Идёт запись", "working")\n'''
new_tick = '''    def _tick(self) -> None:\n        self.recording_seconds += 1\n        hours, remainder = divmod(self.recording_seconds, 3600)\n        minutes, seconds = divmod(remainder, 60)\n        self.duration.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")\n        if self.recorder and self.recorder.active:\n            assessment = self.recording_health_monitor.assess(\n                RecordingHealthSample.from_runtime(\n                    elapsed_seconds=self.recording_seconds,\n                    levels=self.recorder.levels,\n                    health=self.recorder.health,\n                )\n            )\n            sample = assessment.sample\n            self.mic_level.setValue(assessment.microphone_level_percent)\n            self.system_level.setValue(assessment.system_level_percent)\n            self.recording_health_label.setText(\n                f"Очереди: {sample.microphone_queue_percent}% / {sample.system_queue_percent}%; "\n                f"потеряно блоков: {assessment.dropped_blocks}; "\n                f"задержка writer: {sample.max_writer_latency_ms:.1f} мс; "\n                f"тишина: {sample.microphone_silence_seconds:.0f} / "\n                f"{sample.system_silence_seconds:.0f} с; "\n                f"переподключения: {sample.reconnect_attempts}"\n            )\n            if assessment.action == RecordingHealthAction.STOP:\n                self._stop_recording_async(\n                    assessment.stop_reason or "Контроль записи запросил безопасную остановку"\n                )\n                return\n            if assessment.warning_changed and assessment.warnings:\n                self._set_status(\n                    "Проверьте аудио · " + assessment.warning_text,\n                    "warning",\n                )\n                logging.warning("Контроль записи: %s", assessment.warning_text)\n            elif assessment.recovered_from_warning:\n                self._set_status("Идёт запись", "working")\n'''
app = replace_once(app, old_tick, new_tick, label="base _tick policy")

if "_active_audio_warning" in app:
    raise SystemExit("legacy _active_audio_warning remains in base app")
for token in (
    "health.stream_errors",
    "health.microphone_callback_age_seconds",
    "health.system_callback_age_seconds",
):
    if token in new_tick:
        raise SystemExit(f"raw health policy remains in migrated tick: {token}")
APP.write_text(app, encoding="utf-8")

audio = AUDIO.read_text(encoding="utf-8")
audio = replace_once(
    audio,
    '        self._active_audio_warning = ""\n',
    "        self.recording_health_monitor.reset()\n",
    label="recording-start health reset",
)
if "_active_audio_warning" in audio:
    raise SystemExit("legacy _active_audio_warning remains in production audio adapter")
AUDIO.write_text(audio, encoding="utf-8")

# The migration must not survive in the product diff.
WORKFLOW.unlink()
SCRIPT.unlink()
