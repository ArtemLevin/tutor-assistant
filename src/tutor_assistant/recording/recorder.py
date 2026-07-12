from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class RecordingResult:
    microphone_file: Path
    system_file: Path
    mixed_file: Path
    session_file: Path


@dataclass(frozen=True)
class AudioLevels:
    microphone: float = 0.0
    system: float = 0.0


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class ChunkWriter:
    def __init__(
        self,
        directory: Path,
        prefix: str,
        sample_rate: int,
        channels: int,
        chunk_seconds: int,
        on_chunk_closed: Callable[[], None],
    ) -> None:
        import soundfile as sf

        self.sf = sf
        self.directory = directory
        self.prefix = prefix
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_frames = sample_rate * chunk_seconds
        self.on_chunk_closed = on_chunk_closed
        self.index = 0
        self.frames = 0
        self.file = None
        self.directory.mkdir(parents=True, exist_ok=True)
        self._open_next()

    def _open_next(self) -> None:
        path = self.directory / f"{self.prefix}_{self.index:05d}.wav"
        self.file = self.sf.SoundFile(
            path, mode="w", samplerate=self.sample_rate, channels=self.channels, subtype="PCM_16"
        )
        self.frames = 0

    def write(self, data: np.ndarray) -> None:
        cursor = 0
        while cursor < len(data):
            remaining = self.max_frames - self.frames
            part = data[cursor: cursor + remaining]
            self.file.write(part)
            self.frames += len(part)
            cursor += len(part)
            if self.frames >= self.max_frames:
                self.file.flush()
                self.file.close()
                self.index += 1
                self.on_chunk_closed()
                self._open_next()

    def close(self) -> None:
        if self.file is not None:
            self.file.flush()
            self.file.close()
            self.file = None
            self.on_chunk_closed()


class DualRecorder:
    """Crash-tolerant microphone and loopback recorder using finalized WAV chunks."""

    def __init__(self, sample_rate: int = 48_000, channels: int = 1, chunk_seconds: int = 30) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_seconds = chunk_seconds
        self._streams: list[object] = []
        self._writers: list[ChunkWriter] = []
        self._locks = {"microphone": threading.Lock(), "system": threading.Lock()}
        self._session_lock = threading.Lock()
        self._levels = AudioLevels()
        self._level_lock = threading.Lock()
        self._active = False
        self._output_dir: Path | None = None
        self._session_file: Path | None = None
        self._session: dict = {}

    @property
    def active(self) -> bool:
        return self._active

    @property
    def levels(self) -> AudioLevels:
        with self._level_lock:
            return self._levels

    def _update_level(self, source: str, data: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(np.square(data), dtype=np.float64))) if data.size else 0.0
        normalized = min(1.0, max(0.0, rms * 5.0))
        with self._level_lock:
            if source == "microphone":
                self._levels = AudioLevels(normalized, self._levels.system)
            else:
                self._levels = AudioLevels(self._levels.microphone, normalized)

    def _write_session(self) -> None:
        if self._session_file:
            with self._session_lock:
                self._session["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._session["microphone_chunks"] = len(
                    list((self._output_dir / "chunks" / "microphone").glob("*.wav"))
                )
                self._session["system_chunks"] = len(
                    list((self._output_dir / "chunks" / "system").glob("*.wav"))
                )
                _atomic_json(self._session_file, self._session)

    def start(self, output_dir: Path, mic_device: int, loopback_device: int) -> None:
        if self._active:
            raise RuntimeError("Запись уже запущена")
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("Установите sounddevice и soundfile") from exc

        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir = output_dir
        self._session_file = output_dir / "session.json"
        self._session = {
            "version": 1,
            "status": "recording",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "chunk_seconds": self.chunk_seconds,
            "mic_device": mic_device,
            "loopback_device": loopback_device,
        }
        self._write_session()

        mic_writer = ChunkWriter(
            output_dir / "chunks" / "microphone", "mic", self.sample_rate, self.channels,
            self.chunk_seconds, self._write_session,
        )
        system_writer = ChunkWriter(
            output_dir / "chunks" / "system", "system", self.sample_rate, self.channels,
            self.chunk_seconds, self._write_session,
        )

        def callback(source: str, writer: ChunkWriter):
            def write(indata, frames, time_info, status):
                if status:
                    logging.warning("Audio callback %s: %s", source, status)
                data = indata.copy()
                self._update_level(source, data)
                with self._locks[source]:
                    writer.write(data)
            return write

        try:
            mic_stream = sd.InputStream(
                device=mic_device, samplerate=self.sample_rate, channels=self.channels,
                dtype="float32", callback=callback("microphone", mic_writer),
            )
            system_stream = sd.InputStream(
                device=loopback_device, samplerate=self.sample_rate, channels=self.channels,
                dtype="float32", callback=callback("system", system_writer),
            )
            mic_stream.start()
            system_stream.start()
        except Exception:
            mic_writer.close()
            system_writer.close()
            self._session["status"] = "failed_to_start"
            self._write_session()
            raise
        self._streams = [mic_stream, system_stream]
        self._writers = [mic_writer, system_writer]
        self._active = True

    def stop(self) -> RecordingResult:
        if not self._active or self._output_dir is None or self._session_file is None:
            raise RuntimeError("Активная запись отсутствует")
        for stream in self._streams:
            stream.stop()
            stream.close()
        for source, writer in zip(("microphone", "system"), self._writers, strict=True):
            with self._locks[source]:
                writer.close()
        self._active = False
        result = recover_recording(self._output_dir)
        self._session["status"] = "completed"
        self._session["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._write_session()
        return result


def _valid_chunks(directory: Path) -> list[Path]:
    import soundfile as sf

    valid: list[Path] = []
    for path in sorted(directory.glob("*.wav")):
        try:
            info = sf.info(path)
            if info.frames > 0:
                valid.append(path)
        except Exception:
            logging.warning("Пропускаю повреждённый чанк: %s", path)
    return valid


def concatenate_chunks(chunks: list[Path], output: Path, sample_rate: int, channels: int) -> None:
    import soundfile as sf

    if not chunks:
        raise RuntimeError(f"Пригодные аудиочанки отсутствуют для {output.name}")
    with sf.SoundFile(output, "w", samplerate=sample_rate, channels=channels, subtype="PCM_16") as target:
        for path in chunks:
            data, rate = sf.read(path, dtype="float32", always_2d=True)
            if rate != sample_rate:
                raise RuntimeError(f"Частота чанка {path} не совпадает с сессией")
            target.write(data[:, :channels])


def mix_tracks(microphone: Path, system: Path, output: Path) -> None:
    if shutil.which("ffmpeg"):
        subprocess.run([
            "ffmpeg", "-y", "-i", str(microphone), "-i", str(system),
            "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0",
            "-c:a", "pcm_s16le", str(output),
        ], check=True, capture_output=True)
        return
    import soundfile as sf

    mic, rate_mic = sf.read(microphone, always_2d=True)
    system_data, rate_system = sf.read(system, always_2d=True)
    if rate_mic != rate_system:
        raise RuntimeError("Для смешивания без FFmpeg частоты дорожек должны совпадать")
    size = max(len(mic), len(system_data))
    result = np.zeros((size, max(mic.shape[1], system_data.shape[1])))
    result[: len(mic), : mic.shape[1]] += mic
    result[: len(system_data), : system_data.shape[1]] += system_data
    peak = float(np.max(np.abs(result))) or 1.0
    if peak > 1:
        result /= peak
    sf.write(output, result, rate_mic, subtype="PCM_16")


def recover_recording(output_dir: Path) -> RecordingResult:
    session_file = output_dir / "session.json"
    if not session_file.exists():
        raise RuntimeError(f"Манифест записи не найден: {session_file}")
    session = json.loads(session_file.read_text(encoding="utf-8"))
    sample_rate = int(session["sample_rate"])
    channels = int(session["channels"])
    microphone_file = output_dir / "microphone.wav"
    system_file = output_dir / "system.wav"
    mixed_file = output_dir / "lesson.wav"
    concatenate_chunks(
        _valid_chunks(output_dir / "chunks" / "microphone"), microphone_file, sample_rate, channels
    )
    concatenate_chunks(
        _valid_chunks(output_dir / "chunks" / "system"), system_file, sample_rate, channels
    )
    mix_tracks(microphone_file, system_file, mixed_file)
    return RecordingResult(microphone_file, system_file, mixed_file, session_file)


def find_recoverable_recordings(workspace: Path) -> list[Path]:
    sessions: list[Path] = []
    for manifest in workspace.glob("lessons/*/recording/session.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") == "recording" and any((manifest.parent / "chunks").rglob("*.wav")):
            sessions.append(manifest.parent)
    return sorted(sessions)


def level_to_db(level: float) -> float:
    return 20.0 * math.log10(max(level, 1e-6))
