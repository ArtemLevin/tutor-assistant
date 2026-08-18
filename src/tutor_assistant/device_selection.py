from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class AudioInputSelectionCandidate(Protocol):
    @property
    def index(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def host_api(self) -> str: ...

    @property
    def is_wasapi(self) -> bool: ...


def resolve_input_device_identity(
    devices: Sequence[AudioInputSelectionCandidate],
    *,
    device_index: int | None = None,
    device_name: str | None = None,
    host_api: str | None = None,
    prefer_wasapi: bool = True,
) -> AudioInputSelectionCandidate | None:
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
