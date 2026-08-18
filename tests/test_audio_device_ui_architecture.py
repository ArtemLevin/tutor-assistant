"""Architecture gates for audio-device discovery and hot-plug ownership."""

from __future__ import annotations

from pathlib import Path

from tutor_assistant import application
from tutor_assistant.application.audio_devices import (
    AudioDeviceInventory,
    AudioDeviceSelection,
    AudioInputDeviceSnapshot,
    RefreshAudioDevicesUseCase,
    SystemAudioSourceSnapshot,
)


def test_application_device_boundary_is_qt_and_hardware_api_independent() -> None:
    source = Path("src/tutor_assistant/application/audio_devices.py").read_text(
        encoding="utf-8"
    )

    assert "PySide6" not in source
    assert "sounddevice" not in source
    assert "soundcard" not in source
    assert "from ..recording" not in source
    assert "import tutor_assistant.recording" not in source


def test_device_boundary_is_exported_by_application_package() -> None:
    assert application.AudioDeviceInventory is AudioDeviceInventory
    assert application.AudioDeviceSelection is AudioDeviceSelection
    assert application.AudioInputDeviceSnapshot is AudioInputDeviceSnapshot
    assert application.RefreshAudioDevicesUseCase is RefreshAudioDevicesUseCase
    assert application.SystemAudioSourceSnapshot is SystemAudioSourceSnapshot


def test_base_ui_has_no_hardware_discovery_dependency() -> None:
    source = Path("src/tutor_assistant/ui/app.py").read_text(encoding="utf-8")

    assert "list_input_devices" not in source
    assert "list_system_audio_sources" not in source
    assert "probe_input_device" not in source
    assert "resolve_input_device" not in source
    assert "from ..recording import" not in source
    assert "self.devices: list[AudioInputDeviceSnapshot] = []" in source
    assert "self.system_sources: list[SystemAudioSourceSnapshot] = []" in source


def test_production_audio_adapter_owns_discovery_refresh_and_probe() -> None:
    source = Path("src/tutor_assistant/ui/audio_resilient_app.py").read_text(
        encoding="utf-8"
    )

    assert "RefreshAudioDevicesUseCase" in source
    assert "list_input_devices" in source
    assert "list_system_audio_sources" in source
    assert "probe_input_device" in source
    assert "self.audio_devices_use_case.refresh(" in source
    assert "resolve_input_device(" not in source
    assert "probe_input_device(microphone" not in source


def test_quick_start_depends_on_application_snapshot_contracts() -> None:
    source = Path("src/tutor_assistant/quick_start.py").read_text(encoding="utf-8")

    assert "AudioInputDeviceSnapshot" in source
    assert "SystemAudioSourceSnapshot" in source
    assert "resolve_input_device_identity" in source
    assert "from .recording" not in source


def test_recording_device_resolver_delegates_to_application_identity_rule() -> None:
    source = Path("src/tutor_assistant/recording/devices.py").read_text(encoding="utf-8")

    assert "resolve_input_device_identity" in source
    assert "return cast(" in source
