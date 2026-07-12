from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep

import numpy as np

from .devices import SystemAudioSource


@dataclass(frozen=True)
class DeviceTestResult:
    device: int | str
    peak: float
    rms: float
    clipped: bool
    silent: bool


def _result(device: int | str, values: list[np.ndarray]) -> DeviceTestResult:
    if not values:
        return DeviceTestResult(device, 0.0, 0.0, False, True)
    data = np.concatenate(values)
    peak = float(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(np.square(data), dtype=np.float64)))
    return DeviceTestResult(device, peak, rms, peak >= 0.98, rms < 0.002)


def test_input_device(
    device: int, seconds: float = 3.0, sample_rate: int | None = None, channels: int = 1
) -> DeviceTestResult:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Установите sounddevice") from exc
    values: list[np.ndarray] = []
    effective_rate = sample_rate or int(round(float(sd.query_devices(device)["default_samplerate"])))

    def callback(indata, frames, time_info, status):
        values.append(indata.copy())

    with sd.InputStream(
        device=device, samplerate=effective_rate, channels=channels, dtype="float32", callback=callback
    ):
        deadline = monotonic() + seconds
        while monotonic() < deadline:
            sleep(min(0.1, deadline - monotonic()))
    return _result(device, values)


def test_system_audio_source(
    source: SystemAudioSource, seconds: float = 3.0, sample_rate: int | None = None
) -> DeviceTestResult:
    effective_rate = sample_rate or source.default_sample_rate
    if source.backend == "sounddevice":
        if source.legacy_index is None:
            raise RuntimeError("Для fallback-источника отсутствует индекс устройства")
        return test_input_device(source.legacy_index, seconds, effective_rate, 1)
    try:
        import soundcard as sc
    except Exception as exc:
        raise RuntimeError(f"SoundCard недоступен: {exc}") from exc
    device = sc.get_microphone(source.device_id, include_loopback=True)
    frames = max(1, round(seconds * effective_rate))
    with device.recorder(samplerate=effective_rate, blocksize=max(2048, effective_rate // 5)) as recorder:
        data = np.asarray(recorder.record(numframes=frames), dtype="float32")
    if data.ndim == 1:
        data = data[:, np.newaxis]
    return _result(source.device_id, [data])
