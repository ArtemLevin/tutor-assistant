from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RecordingResult:
    microphone_file: Path
    system_file: Path
    mixed_file: Path


class DualRecorder:
    """Records microphone and a Windows loopback input into separate WAV files."""

    def __init__(self, sample_rate: int = 48_000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._streams: list[object] = []
        self._files: list[object] = []
        self._lock = threading.Lock()
        self._active = False
        self._paths: tuple[Path, Path] | None = None

    def start(self, output_dir: Path, mic_device: int, loopback_device: int) -> None:
        if self._active:
            raise RuntimeError("Запись уже запущена")
        try:
            import sounddevice as sd
            import soundfile as sf
        except ImportError as exc:
            raise RuntimeError("Установите sounddevice и soundfile") from exc

        output_dir.mkdir(parents=True, exist_ok=True)
        mic_path = output_dir / "microphone.wav"
        system_path = output_dir / "system.wav"
        mic_file = sf.SoundFile(mic_path, mode="w", samplerate=self.sample_rate, channels=self.channels)
        sys_file = sf.SoundFile(system_path, mode="w", samplerate=self.sample_rate, channels=self.channels)

        def callback(target):
            def write(indata, frames, time_info, status):
                if status:
                    logging.warning("Audio callback: %s", status)
                with self._lock:
                    target.write(indata.copy())
            return write

        try:
            mic_stream = sd.InputStream(
                device=mic_device,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                callback=callback(mic_file),
            )
            sys_stream = sd.InputStream(
                device=loopback_device,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                callback=callback(sys_file),
            )
            mic_stream.start()
            sys_stream.start()
        except Exception:
            mic_file.close()
            sys_file.close()
            raise
        self._streams = [mic_stream, sys_stream]
        self._files = [mic_file, sys_file]
        self._paths = (mic_path, system_path)
        self._active = True

    def stop(self) -> RecordingResult:
        if not self._active or self._paths is None:
            raise RuntimeError("Активная запись отсутствует")
        for stream in self._streams:
            stream.stop()
            stream.close()
        for file in self._files:
            file.flush()
            file.close()
        self._active = False
        mic_path, system_path = self._paths
        mixed_path = mic_path.parent / "lesson.wav"
        self._mix(mic_path, system_path, mixed_path)
        return RecordingResult(mic_path, system_path, mixed_path)

    def _mix(self, mic_path: Path, system_path: Path, output_path: Path) -> None:
        if shutil.which("ffmpeg"):
            command = [
                "ffmpeg", "-y", "-i", str(mic_path), "-i", str(system_path),
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0",
                "-c:a", "pcm_s16le", str(output_path),
            ]
            subprocess.run(command, check=True, capture_output=True)
            return
        import soundfile as sf
        import numpy as np

        mic, rate_mic = sf.read(mic_path, always_2d=True)
        system, rate_system = sf.read(system_path, always_2d=True)
        if rate_mic != rate_system:
            raise RuntimeError("Для смешивания без FFmpeg частоты устройств должны совпадать")
        size = max(len(mic), len(system))
        result = np.zeros((size, max(mic.shape[1], system.shape[1])))
        result[: len(mic), : mic.shape[1]] += mic
        result[: len(system), : system.shape[1]] += system
        peak = float(np.max(np.abs(result))) or 1.0
        if peak > 1:
            result /= peak
        sf.write(output_path, result, rate_mic, subtype="PCM_16")

