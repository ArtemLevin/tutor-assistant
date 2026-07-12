from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep

import numpy as np


@dataclass(frozen=True)
class DeviceTestResult:
    device: int
    peak: float
    rms: float
    clipped: bool
    silent: bool


def test_input_device(device: int, seconds: float = 3.0, sample_rate: int = 48_000, channels: int = 1) -> DeviceTestResult:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Установите sounddevice") from exc
    values: list[np.ndarray] = []

    def callback(indata, frames, time_info, status):
        values.append(indata.copy())

    with sd.InputStream(
        device=device, samplerate=sample_rate, channels=channels, dtype="float32", callback=callback
    ):
        deadline = monotonic() + seconds
        while monotonic() < deadline:
            sleep(min(0.1, deadline - monotonic()))
    if not values:
        return DeviceTestResult(device, 0.0, 0.0, False, True)
    data = np.concatenate(values)
    peak = float(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(np.square(data), dtype=np.float64)))
    return DeviceTestResult(device, peak, rms, peak >= 0.98, rms < 0.002)

