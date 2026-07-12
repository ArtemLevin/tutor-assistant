from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    max_input_channels: int
    default_sample_rate: int
    host_api: str

    @property
    def likely_loopback(self) -> bool:
        name = self.name.lower()
        return "loopback" in name or "стерео микшер" in name or "stereo mix" in name


def list_input_devices() -> list[AudioDevice]:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Установите sounddevice для работы со звуком") from exc
    host_apis = sd.query_hostapis()
    result: list[AudioDevice] = []
    for index, raw in enumerate(sd.query_devices()):
        if int(raw["max_input_channels"]) < 1:
            continue
        result.append(
            AudioDevice(
                index=index,
                name=str(raw["name"]),
                max_input_channels=int(raw["max_input_channels"]),
                default_sample_rate=int(raw["default_samplerate"]),
                host_api=str(host_apis[int(raw["hostapi"])]["name"]),
            )
        )
    return result
