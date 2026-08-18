from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ..device_selection import AudioInputSelectionCandidate, resolve_input_device_identity


class AudioInputDeviceSnapshot(AudioInputSelectionCandidate, Protocol):
    """Read-only microphone snapshot exposed across the application boundary."""

    @property
    def max_input_channels(self) -> int: ...

    @property
    def default_sample_rate(self) -> int: ...


class SystemAudioSourceSnapshot(Protocol):
    """Read-only system-audio snapshot exposed across the application boundary."""

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
        return AudioDeviceInventory(
            input_devices=devices,
            system_sources=sources,
            microphone=microphone,
            system_source=system_source,
        )

    def probe_microphone(
        self,
        microphone: AudioInputDeviceSnapshot,
        *,
        channels: int = 1,
    ) -> None:
        """Validate the selected live endpoint through the injected hardware adapter."""

        self._probe_input_device(microphone, channels=channels)

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
