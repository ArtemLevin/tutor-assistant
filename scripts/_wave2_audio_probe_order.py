from pathlib import Path

path = Path("src/tutor_assistant/ui/audio_resilient_app.py")
text = path.read_text(encoding="utf-8")
old = '''        inventory = self.audio_devices_use_case.refresh(
            selection,
            target_sample_rate=self.config.recording.target_sample_rate,
            channels=self.config.recording.channels,
            probe=probe,
        )
'''
new = '''        inventory = self.audio_devices_use_case.refresh(
            selection,
            target_sample_rate=self.config.recording.target_sample_rate,
        )
'''
if text.count(old) != 1:
    raise RuntimeError(f"expected one refresh block, found {text.count(old)}")
text = text.replace(old, new, 1)

marker = '''        if require_ready and system_source is None:
            raise RuntimeError(
                "Сохранённый источник системного звука больше не найден. "
                "Выберите WASAPI Loopback-устройство заново и повторите проверку аудио."
            )
'''
replacement = marker + '''        if probe and microphone is not None:
            self.audio_devices_use_case.probe_microphone(
                microphone,
                channels=self.config.recording.channels,
            )
'''
if text.count(marker) != 1:
    raise RuntimeError(f"expected one readiness marker, found {text.count(marker)}")
text = text.replace(marker, replacement, 1)

if "probe_input_device(microphone" in text:
    raise RuntimeError("production adapter still probes hardware directly")
if "self.audio_devices_use_case.probe_microphone(" not in text:
    raise RuntimeError("application probe boundary missing")
path.write_text(text, encoding="utf-8")
