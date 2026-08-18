from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}: {old[:80]!r}")
    write(path, text.replace(old, new, 1))


# application/__init__.py
replace_once(
    "src/tutor_assistant/application/__init__.py",
    "from .audio_preflight import AudioPreflightResult, AudioPreflightUseCase\n",
    "from .audio_devices import (\n"
    "    AudioDeviceInventory,\n"
    "    AudioDeviceSelection,\n"
    "    AudioInputDeviceSnapshot,\n"
    "    RefreshAudioDevicesUseCase,\n"
    "    SystemAudioSourceSnapshot,\n"
    ")\n"
    "from .audio_preflight import AudioPreflightResult, AudioPreflightUseCase\n",
)
replace_once(
    "src/tutor_assistant/application/__init__.py",
    "__all__ = [\n    \"AudioPreflightResult\",\n",
    "__all__ = [\n"
    "    \"AudioDeviceInventory\",\n"
    "    \"AudioDeviceSelection\",\n"
    "    \"AudioInputDeviceSnapshot\",\n"
    "    \"AudioPreflightResult\",\n"
    "    \"RefreshAudioDevicesUseCase\",\n"
    "    \"SystemAudioSourceSnapshot\",\n",
)

# quick_start.py
replace_once(
    "src/tutor_assistant/quick_start.py",
    "from dataclasses import dataclass\n\nfrom .config import AppConfig, LaunchProfile\n"
    "from .domain import Student\n"
    "from .recording import AudioDevice, SystemAudioSource\n"
    "from .recording.devices import resolve_input_device\n",
    "from collections.abc import Sequence\n"
    "from dataclasses import dataclass\n\n"
    "from .application.audio_devices import (\n"
    "    AudioInputDeviceSnapshot,\n"
    "    SystemAudioSourceSnapshot,\n"
    "    resolve_input_device_identity,\n"
    ")\n"
    "from .config import AppConfig, LaunchProfile\n"
    "from .domain import Student\n",
)
replace_once(
    "src/tutor_assistant/quick_start.py",
    "    devices: list[AudioDevice],\n    system_sources: list[SystemAudioSource],\n",
    "    devices: Sequence[AudioInputDeviceSnapshot],\n"
    "    system_sources: Sequence[SystemAudioSourceSnapshot],\n",
)
replace_once(
    "src/tutor_assistant/quick_start.py",
    "    microphone = resolve_input_device(\n",
    "    microphone = resolve_input_device_identity(\n",
)

# recording/devices.py delegates stable identity to the application rule.
replace_once(
    "src/tutor_assistant/recording/devices.py",
    "from dataclasses import asdict, dataclass\n\n\n@dataclass(frozen=True)\n",
    "from dataclasses import asdict, dataclass\n"
    "from typing import cast\n\n"
    "from ..application.audio_devices import resolve_input_device_identity\n\n\n"
    "@dataclass(frozen=True)\n",
)
devices_path = "src/tutor_assistant/recording/devices.py"
devices_text = read(devices_path)
start = devices_text.index("def resolve_input_device(\n")
end = devices_text.index("\ndef probe_input_device", start)
new_resolver = '''def resolve_input_device(
    devices: list[AudioDevice],
    *,
    device_index: int | None = None,
    device_name: str | None = None,
    host_api: str | None = None,
    prefer_wasapi: bool = True,
) -> AudioDevice | None:
    """Resolve a live microphone without treating the PortAudio index as persistent identity."""

    return cast(
        AudioDevice | None,
        resolve_input_device_identity(
            devices,
            device_index=device_index,
            device_name=device_name,
            host_api=host_api,
            prefer_wasapi=prefer_wasapi,
        ),
    )

'''
devices_text = devices_text[:start] + new_resolver + devices_text[end + 1 :]
write(devices_path, devices_text)

# Base UI becomes a presentation-only consumer of application snapshots.
replace_once(
    "src/tutor_assistant/ui/app.py",
    "from ..application import RecordingRuntimeRecorder\n",
    "from ..application import (\n"
    "    AudioInputDeviceSnapshot,\n"
    "    RecordingRuntimeRecorder,\n"
    "    SystemAudioSourceSnapshot,\n"
    ")\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "from ..recording import (\n"
    "    SystemAudioSource,\n"
    "    list_input_devices,\n"
    "    list_system_audio_sources,\n"
    ")\n",
    "",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "        self.devices = list_input_devices()\n"
    "        self.system_sources = list_system_audio_sources(\n"
    "            self.devices, self.config.recording.target_sample_rate\n"
    "        )\n",
    "        self.devices: list[AudioInputDeviceSnapshot] = []\n"
    "        self.system_sources: list[SystemAudioSourceSnapshot] = []\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "            if not isinstance(source, SystemAudioSource):\n                continue\n",
    "            if source is None:\n                continue\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "        if isinstance(source, SystemAudioSource):\n",
    "        if source is not None:\n",
)

# Production audio adapter owns discovery/resolution/probe orchestration.
replace_once(
    "src/tutor_assistant/ui/audio_resilient_app.py",
    "from ..application import AudioPreflightResult, AudioPreflightUseCase\n",
    "from ..application import (\n"
    "    AudioDeviceSelection,\n"
    "    AudioPreflightResult,\n"
    "    AudioPreflightUseCase,\n"
    "    RefreshAudioDevicesUseCase,\n"
    ")\n",
)
replace_once(
    "src/tutor_assistant/ui/audio_resilient_app.py",
    "from ..recording.devices import probe_input_device, resolve_input_device\n",
    "from ..recording.devices import probe_input_device\n",
)
replace_once(
    "src/tutor_assistant/ui/audio_resilient_app.py",
    "        super().__init__(config_path)\n"
    "        self.start_recording_use_case = StartRecordingUseCase(\n",
    "        super().__init__(config_path)\n"
    "        self.audio_devices_use_case = RefreshAudioDevicesUseCase(\n"
    "            list_input_devices,\n"
    "            list_system_audio_sources,\n"
    "            probe_input_device,\n"
    "        )\n"
    "        self.start_recording_use_case = StartRecordingUseCase(\n",
)
audio_path = "src/tutor_assistant/ui/audio_resilient_app.py"
audio_text = read(audio_path)
block_start = audio_text.index(
    '        current_system = self.loopback.currentData() if hasattr(self, "loopback") else None\n'
)
block_end = audio_text.index("        self.devices = devices\n", block_start)
new_refresh_block = '''        current_system = self.loopback.currentData() if hasattr(self, "loopback") else None
        selection = AudioDeviceSelection(
            microphone_index=self.config.recording.mic_device,
            microphone_name=self.config.recording.mic_device_name,
            microphone_host_api=self.config.recording.mic_host_api,
            system_device_id=(
                current_system.device_id
                if isinstance(current_system, SystemAudioSource)
                else self.config.recording.system_device_id
            ),
            system_backend=(
                current_system.backend
                if isinstance(current_system, SystemAudioSource)
                else self.config.recording.system_backend
            ),
            legacy_loopback_index=self.config.recording.loopback_device,
        )
        inventory = self.audio_devices_use_case.refresh(
            selection,
            target_sample_rate=self.config.recording.target_sample_rate,
            channels=self.config.recording.channels,
            probe=probe,
        )
        devices = list(inventory.input_devices)
        sources = list(inventory.system_sources)
        microphone = inventory.microphone
        system_source = cast(SystemAudioSource | None, inventory.system_source)

'''
audio_text = audio_text[:block_start] + new_refresh_block + audio_text[block_end:]
write(audio_path, audio_text)
replace_once(
    audio_path,
    "        if require_ready and microphone is None:\n",
    "        self._refresh_quick_readiness()\n\n"
    "        if require_ready and microphone is None:\n",
)
replace_once(
    audio_path,
    "        if probe and microphone is not None:\n"
    "            probe_input_device(microphone, channels=self.config.recording.channels)\n",
    "",
)

# Architecture gate allows the application snapshot protocol name while banning infrastructure import.
replace_once(
    "tests/test_audio_device_ui_architecture.py",
    '    assert "SystemAudioSource" not in source\n',
    '    assert "from ..recording import" not in source\n',
)

# Fail fast if the old boundaries survived or the new ownership was not established.
app_source = read("src/tutor_assistant/ui/app.py")
for forbidden in (
    "list_input_devices",
    "list_system_audio_sources",
    "probe_input_device",
    "resolve_input_device",
    "from ..recording import",
    "isinstance(source, SystemAudioSource)",
):
    if forbidden in app_source:
        raise RuntimeError(f"base UI still contains forbidden audio infrastructure dependency: {forbidden}")
if "self.devices: list[AudioInputDeviceSnapshot] = []" not in app_source:
    raise RuntimeError("base UI does not initialize structural input snapshots")
if "self.system_sources: list[SystemAudioSourceSnapshot] = []" not in app_source:
    raise RuntimeError("base UI does not initialize structural system snapshots")

audio_source = read(audio_path)
for required in (
    "RefreshAudioDevicesUseCase",
    "self.audio_devices_use_case.refresh(",
    "list_input_devices",
    "list_system_audio_sources",
    "probe_input_device",
):
    if required not in audio_source:
        raise RuntimeError(f"production audio adapter missing required boundary ownership: {required}")
for forbidden in (
    "resolve_input_device(",
    "probe_input_device(microphone",
):
    if forbidden in audio_source:
        raise RuntimeError(f"production adapter still bypasses use case: {forbidden}")

quick_source = read("src/tutor_assistant/quick_start.py")
if "from .recording" in quick_source or "resolve_input_device(" in quick_source:
    raise RuntimeError("quick-start readiness still depends on recording infrastructure")

application_source = read("src/tutor_assistant/application/audio_devices.py")
for forbidden in ("PySide6", "sounddevice", "soundcard", "from ..recording"):
    if forbidden in application_source:
        raise RuntimeError(f"application device boundary leaked infrastructure: {forbidden}")
