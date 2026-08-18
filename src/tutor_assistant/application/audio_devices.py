from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class AudioInputDeviceSnapshot(Protocol):
    """Read-only microphone identity exposed across the application boundary."""

    @property
    def index(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def max_input_channels(self) -> int: ...

    @property
    def default_sample_rate(self) -> int: ...

    @property
    def host_api(self) -> str: ...

    @property
    def is_wasapi(self) -> bool: ...


class SystemAudioSourceSnapshot(Protocol):
    """Read-only system-audio identity exposed across the application boundary."""

    @property
    def device_id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def backend(self) -> str: ...

    @property
    def channels(self) -> int: ...

    @property
    def default_sample_rate(self) -> int: ...

    @property
    def is_default(self) -> bool: ...

    @property
    def legacy_index(self) -> int | None: ...

    @property
    def key(self) -> str: ...

    @property
    def display_name(self) -> str: ...


class AudioInputDeviceLister(Protocol):
    def __call__(self) -> list[AudioInputDeviceSnapshot]: ...


class SystemAudioSourceLister(Protocol):
    def __call__(
        self,
        input_devices: list[AudioInputDeviceSnapshot],
        sample_rate: int,
    ) -> list[SystemAudioSourceSnapshot]: ...


class AudioInputDeviceProbe(Protocol):
    def __call__(
        self,
        device: AudioInputDeviceSnapshot,
        *,
        channels: int = 1,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AudioDeviceSelection:
    """Persistent audio identity used to resolve a fresh hardware snapshot."""

    microphone_index: int | None = None
    microphone_name: str | None = None
    microphone_host_api: str | None = None
    system_device_id: str | None = None
    system_backend: str | None = None
    legacy_loopback_index: int | None = None


@dataclass(frozen=True, slots=True)
class AudioDeviceInventory:
    """One coherent discovery result used by presentation and readiness checks."""

    input_devices: tuple[AudioInputDeviceSnapshot, ...]
    system_sources: tuple[SystemAudioSourceSnapshot, ...]
    microphone: AudioInputDeviceSnapshot | None
    system_source: SystemAudioSourceSnapshot | None


def resolve_input_device_identity(
    devices: Sequence[AudioInputDeviceSnapshot],
    *,
    device_index: int | None = None,
    device_name: str | None = None,
    host_api: str | None = None,
    prefer_wasapi: bool = True,
) -> AudioInputDeviceSnapshot | None:
    """Resolve a microphone by stable identity rather than a transient PortAudio index."""

    if not devices:
        return None

    normalized_name = (device_name or "").strip().casefold()
    normalized_host = (host_api or "").strip().casefold()

    if normalized_name:
        named = [device for device in devices if device.name.strip().casefold() == normalized_name]
        if not named:
            return None
        exact_host = [
            device for device in named if device.host_api.strip().casefold() == normalized_host
        ]
        if prefer_wasapi:
            wasapi = [device for device in named if device.is_wasapi]
            if wasapi and (not exact_host or not exact_host[0].is_wasapi):
                return min(wasapi, key=lambda device: device.index)
        if exact_host:
            return min(exact_host, key=lambda device: device.index)
        wasapi = [device for device in named if device.is_wasapi]
        if wasapi:
            return min(wasapi, key=lambda device: device.index)
        return min(named, key=lambda device: device.index)

    indexed = next((device for device in devices if device.index == device_index), None)
    if indexed is None:
        return None
    if prefer_wasapi and not indexed.is_wasapi:
        same_name_wasapi = [
            device
            for device in devices
            if device.name.strip().casefold() == indexed.name.strip().casefold()
            and device.is_wasapi
        ]
        if same_name_wasapi:
            return min(same_name_wasapi, key=lambda device: device.index)
    return indexed


class RefreshAudioDevicesUseCase:
    """Discover and resolve live audio endpoints without exposing hardware APIs to UI code."""

    def __init__(
        self,
        list_input_devices: AudioInputDeviceLister,
        list_system_audio_sources: SystemAudioSourceLister,
        probe_input_device: AudioInputDeviceProbe,
    ) -> None:
        self._list_input_devices = list_input_devices
        self._list_system_audio_sources = list_system_audio_sources
        self._probe_input_device = probe_input_device

    def refresh(
        self,
        selection: AudioDeviceSelection,
        *,
        target_sample_rate: int,
        channels: int = 1,
        probe: bool = False,
    ) -> AudioDeviceInventory:
        devices = tuple(self._list_input_devices())
        sources = tuple(
            self._list_system_audio_sources(
                list(devices),
                target_sample_rate,
            )
        )
        microphone = resolve_input_device_identity(
            devices,
            device_index=selection.microphone_index,
            device_name=selection.microphone_name,
            host_api=selection.microphone_host_api,
        )
        system_source = self._resolve_system_source(sources, selection)
        if probe and microphone is not None:
            self._probe_input_device(microphone, channels=channels)
        return AudioDeviceInventory(
            input_devices=devices,
            system_sources=sources,
            microphone=microphone,
            system_source=system_source,
        )

    @staticmethod
    def _resolve_system_source(
        sources: Sequence[SystemAudioSourceSnapshot],
        selection: AudioDeviceSelection,
    ) -> SystemAudioSourceSnapshot | None:
        system_source = next(
            (
                source
                for source in sources
                if source.device_id == selection.system_device_id
                and source.backend == selection.system_backend
            ),
            None,
        )
        if (
            system_source is None
            and selection.system_device_id is None
            and selection.legacy_loopback_index is not None
        ):
            system_source = next(
                (
                    source
                    for source in sources
                    if source.legacy_index == selection.legacy_loopback_index
                ),
                None,
            )
        if system_source is None and selection.system_device_id is None and sources:
            system_source = sources[0]
        return system_source
