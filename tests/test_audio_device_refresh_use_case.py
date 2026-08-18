from __future__ import annotations

from dataclasses import dataclass

from tutor_assistant.application.audio_devices import (
    AudioDeviceSelection,
    RefreshAudioDevicesUseCase,
    resolve_input_device_identity,
)


@dataclass(frozen=True)
class FakeInputDevice:
    index: int
    name: str
    max_input_channels: int = 1
    default_sample_rate: int = 48_000
    host_api: str = "Windows WASAPI"

    @property
    def is_wasapi(self) -> bool:
        return "wasapi" in self.host_api.casefold()


@dataclass(frozen=True)
class FakeSystemSource:
    device_id: str
    name: str
    backend: str = "soundcard"
    channels: int = 2
    default_sample_rate: int = 48_000
    is_default: bool = False
    legacy_index: int | None = None

    @property
    def key(self) -> str:
        return f"{self.backend}:{self.device_id}"

    @property
    def display_name(self) -> str:
        return self.name


def test_identity_resolution_survives_portaudio_reindex_and_prefers_wasapi() -> None:
    devices = [
        FakeInputDevice(12, "Microphone FIFINE", host_api="MME"),
        FakeInputDevice(41, "Microphone FIFINE", host_api="Windows WASAPI"),
    ]

    resolved = resolve_input_device_identity(
        devices,
        device_index=12,
        device_name="Microphone FIFINE",
        host_api="MME",
    )

    assert resolved is devices[1]


def test_refresh_discovers_and_resolves_one_coherent_inventory_before_probe() -> None:
    events: list[object] = []
    devices = [FakeInputDevice(41, "Microphone FIFINE")]
    sources = [
        FakeSystemSource("speakers", "Speakers"),
        FakeSystemSource("g733", "G733"),
    ]

    def list_inputs() -> list[FakeInputDevice]:
        events.append("inputs")
        return devices

    def list_sources(input_devices, sample_rate: int):
        events.append(("sources", input_devices, sample_rate))
        return sources

    def probe(device, *, channels: int = 1) -> None:
        events.append(("probe", device, channels))

    use_case = RefreshAudioDevicesUseCase(list_inputs, list_sources, probe)
    inventory = use_case.refresh(
        AudioDeviceSelection(
            microphone_index=7,
            microphone_name="Microphone FIFINE",
            microphone_host_api="Windows WASAPI",
            system_device_id="g733",
            system_backend="soundcard",
        ),
        target_sample_rate=48_000,
    )

    assert inventory.input_devices == tuple(devices)
    assert inventory.system_sources == tuple(sources)
    assert inventory.microphone is devices[0]
    assert inventory.system_source is sources[1]
    assert events == [
        "inputs",
        ("sources", devices, 48_000),
    ]

    use_case.probe_microphone(devices[0], channels=2)

    assert events[-1] == ("probe", devices[0], 2)


def test_refresh_preserves_legacy_loopback_fallback() -> None:
    devices = [FakeInputDevice(3, "Mic")]
    legacy = FakeSystemSource(
        "27",
        "Stereo Mix",
        backend="sounddevice",
        legacy_index=27,
    )
    use_case = RefreshAudioDevicesUseCase(
        lambda: devices,
        lambda _devices, _rate: [legacy],
        lambda _device, *, channels=1: None,
    )

    inventory = use_case.refresh(
        AudioDeviceSelection(
            microphone_index=3,
            legacy_loopback_index=27,
        ),
        target_sample_rate=48_000,
    )

    assert inventory.system_source is legacy


def test_refresh_uses_first_system_source_only_for_unconfigured_identity() -> None:
    source = FakeSystemSource("default", "Default")
    use_case = RefreshAudioDevicesUseCase(
        lambda: [],
        lambda _devices, _rate: [source],
        lambda _device, *, channels=1: None,
    )

    unconfigured = use_case.refresh(
        AudioDeviceSelection(),
        target_sample_rate=48_000,
    )
    disconnected = use_case.refresh(
        AudioDeviceSelection(
            system_device_id="missing",
            system_backend="soundcard",
        ),
        target_sample_rate=48_000,
    )

    assert unconfigured.system_source is source
    assert disconnected.system_source is None


def test_refresh_has_no_probe_side_effect_when_saved_microphone_is_missing() -> None:
    probe_calls = 0

    def probe(_device, *, channels: int = 1) -> None:
        nonlocal probe_calls
        probe_calls += 1

    use_case = RefreshAudioDevicesUseCase(
        lambda: [FakeInputDevice(5, "Built-in microphone")],
        lambda _devices, _rate: [],
        probe,
    )

    inventory = use_case.refresh(
        AudioDeviceSelection(
            microphone_index=5,
            microphone_name="Disconnected USB microphone",
            microphone_host_api="Windows WASAPI",
        ),
        target_sample_rate=48_000,
    )

    assert inventory.microphone is None
    assert probe_calls == 0
