from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from tutor_assistant.config import AppConfig
from tutor_assistant.domain import Student
from tutor_assistant.quick_start import evaluate_readiness
from tutor_assistant.recording import AudioDevice, SystemAudioSource
from tutor_assistant.recording.devices import probe_input_device, resolve_input_device


def _device(index: int, host_api: str, name: str = "Microphone FIFINE") -> AudioDevice:
    return AudioDevice(index, name, 1, 48_000, host_api)


def test_stable_microphone_identity_survives_portaudio_reindex() -> None:
    devices = [
        _device(6, "MME", "Other microphone"),
        _device(41, "Windows WASAPI"),
    ]

    resolved = resolve_input_device(
        devices,
        device_index=6,
        device_name="Microphone FIFINE",
        host_api="Windows WASAPI",
    )

    assert resolved is not None
    assert resolved.index == 41
    assert resolved.host_api == "Windows WASAPI"


def test_legacy_mme_selection_migrates_to_same_name_wasapi() -> None:
    devices = [
        _device(12, "MME"),
        _device(27, "Windows WASAPI"),
    ]

    resolved = resolve_input_device(devices, device_index=12)

    assert resolved is not None
    assert resolved.index == 27
    assert resolved.is_wasapi


def test_stable_identity_does_not_fall_back_to_unrelated_reused_index() -> None:
    devices = [_device(12, "Windows WASAPI", "Built-in microphone")]

    resolved = resolve_input_device(
        devices,
        device_index=12,
        device_name="Disconnected USB microphone",
        host_api="Windows WASAPI",
    )

    assert resolved is None


def test_quick_start_uses_stable_microphone_identity_after_reindex() -> None:
    config = AppConfig()
    config.recording.mic_device = 12
    config.recording.mic_device_name = "Microphone FIFINE"
    config.recording.mic_host_api = "Windows WASAPI"
    config.recording.system_device_id = "g733"
    students = [Student(id="timofey", full_name="Тимофей")]
    devices = [_device(41, "Windows WASAPI")]
    sources = [SystemAudioSource("g733", "G733", "soundcard", 2, 48_000)]

    result = evaluate_readiness(
        config,
        students,
        devices,
        sources,
        "timofey",
        "Производная",
    )

    assert result.ready
    microphone = next(item for item in result.items if item.code == "microphone")
    assert "Windows WASAPI" in microphone.detail


def test_microphone_identity_round_trip(tmp_path) -> None:
    path = tmp_path / "app.yaml"
    config = AppConfig()
    config.recording.mic_device = 41
    config.recording.mic_device_name = "Microphone FIFINE"
    config.recording.mic_host_api = "Windows WASAPI"

    config.save(path)
    restored = AppConfig.load(path)

    assert restored.recording.mic_device == 41
    assert restored.recording.mic_device_name == "Microphone FIFINE"
    assert restored.recording.mic_host_api == "Windows WASAPI"


def test_probe_opens_and_closes_selected_endpoint(monkeypatch) -> None:
    opened: list[int] = []
    closed: list[bool] = []

    class Stream:
        def __init__(self, **kwargs) -> None:
            opened.append(kwargs["device"])

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=Stream))

    probe_input_device(_device(41, "Windows WASAPI"))

    assert opened == [41]
    assert closed == [True]


def test_probe_translates_portaudio_host_error(monkeypatch) -> None:
    class Stream:
        def __init__(self, **kwargs) -> None:
            raise RuntimeError("MME error 2")

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(InputStream=Stream))

    with pytest.raises(RuntimeError, match="Windows могла перенумеровать"):
        probe_input_device(_device(12, "MME"))
