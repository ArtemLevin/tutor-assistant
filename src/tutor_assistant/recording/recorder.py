from __future__ import annotations

import json
import logging
import math
import queue
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import numpy as np


@dataclass(frozen=True)
class RecordingResult:
    microphone_file: Path
    system_file: Path
    mixed_file: Path
    session_file: Path
    sync_report: Path


@dataclass(frozen=True)
class AudioLevels:
    microphone: float = 0.0
    system: float = 0.0


@dataclass(frozen=True)
class RecorderHealth:
    microphone_queue_percent: int = 0
    system_queue_percent: int = 0
    microphone_dropped_blocks: int = 0
    system_dropped_blocks: int = 0
    max_writer_latency_ms: float = 0.0


@dataclass
class AudioBlock:
    data: np.ndarray
    queued_at: float


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class QueuedChunkWriter:
    """Moves every filesystem operation away from the real-time audio callback."""

    def __init__(
        self,
        directory: Path,
        prefix: str,
        sample_rate: int,
        channels: int,
        chunk_seconds: int,
        queue_blocks: int,
        on_chunk_closed: Callable[[], None],
        on_level: Callable[[float], None],
    ) -> None:
        self.directory = directory
        self.prefix = prefix
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_frames = sample_rate * chunk_seconds
        self.on_chunk_closed = on_chunk_closed
        self.on_level = on_level
        self.queue: queue.Queue[AudioBlock | None] = queue.Queue(maxsize=queue_blocks)
        self.index = 0
        self.frames_in_chunk = 0
        self.total_frames = 0
        self.dropped_blocks = 0
        self.max_latency_seconds = 0.0
        self.first_callback_monotonic: float | None = None
        self.last_callback_monotonic: float | None = None
        self.error: BaseException | None = None
        self.directory.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self._run, name=f"audio-writer-{prefix}", daemon=True)
        self.thread.start()

    @property
    def queue_percent(self) -> int:
        return round(self.queue.qsize() / self.queue.maxsize * 100)

    def enqueue(self, data: np.ndarray, callback_time: float) -> None:
        if self.first_callback_monotonic is None:
            self.first_callback_monotonic = callback_time
        self.last_callback_monotonic = callback_time
        try:
            self.queue.put_nowait(AudioBlock(data.copy(), monotonic()))
        except queue.Full:
            self.dropped_blocks += 1

    def stop(self, timeout: float = 30.0) -> None:
        while True:
            if not self.thread.is_alive():
                break
            try:
                self.queue.put(None, timeout=0.5)
                break
            except queue.Full:
                continue
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise RuntimeError(f"Поток записи {self.prefix} не завершился за {timeout} секунд")
        if self.error:
            raise RuntimeError(f"Ошибка writer-потока {self.prefix}: {self.error}") from self.error

    def _run(self) -> None:
        import soundfile as sf

        file = None
        try:
            file = self._open(sf)
            while True:
                block = self.queue.get()
                if block is None:
                    self.queue.task_done()
                    break
                latency = monotonic() - block.queued_at
                self.max_latency_seconds = max(self.max_latency_seconds, latency)
                rms = float(np.sqrt(np.mean(np.square(block.data), dtype=np.float64)))
                self.on_level(min(1.0, max(0.0, rms * 5.0)))
                cursor = 0
                while cursor < len(block.data):
                    remaining = self.max_frames - self.frames_in_chunk
                    part = block.data[cursor : cursor + remaining]
                    file.write(part)
                    count = len(part)
                    self.frames_in_chunk += count
                    self.total_frames += count
                    cursor += count
                    if self.frames_in_chunk >= self.max_frames:
                        file.flush()
                        file.close()
                        self.index += 1
                        self.on_chunk_closed()
                        file = self._open(sf)
                self.queue.task_done()
        except BaseException as exc:
            self.error = exc
            logging.exception("Ошибка аудиопотока %s", self.prefix)
        finally:
            if file is not None:
                file.flush()
                file.close()
                self.on_chunk_closed()

    def _open(self, sf):
        self.frames_in_chunk = 0
        path = self.directory / f"{self.prefix}_{self.index:05d}.wav"
        return sf.SoundFile(
            path, mode="w", samplerate=self.sample_rate, channels=self.channels, subtype="PCM_16"
        )


class DualRecorder:
    def __init__(
        self,
        sample_rate: int = 48_000,
        channels: int = 1,
        chunk_seconds: int = 30,
        queue_blocks: int = 256,
        target_sample_rate: int = 48_000,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_seconds = chunk_seconds
        self.queue_blocks = queue_blocks
        self.target_sample_rate = target_sample_rate
        self._streams: list[object] = []
        self._writers: dict[str, QueuedChunkWriter] = {}
        self._level_lock = threading.Lock()
        self._session_lock = threading.Lock()
        self._levels = AudioLevels()
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

    @property
    def health(self) -> RecorderHealth:
        mic = self._writers.get("microphone")
        system = self._writers.get("system")
        return RecorderHealth(
            mic.queue_percent if mic else 0,
            system.queue_percent if system else 0,
            mic.dropped_blocks if mic else 0,
            system.dropped_blocks if system else 0,
            round(
                max(
                    mic.max_latency_seconds if mic else 0,
                    system.max_latency_seconds if system else 0,
                )
                * 1000,
                2,
            ),
        )

    def _set_level(self, source: str, value: float) -> None:
        with self._level_lock:
            if source == "microphone":
                self._levels = AudioLevels(value, self._levels.system)
            else:
                self._levels = AudioLevels(self._levels.microphone, value)

    def _write_session(self) -> None:
        if not self._session_file or not self._output_dir:
            return
        with self._session_lock:
            self._session["updated_at"] = datetime.now(UTC).isoformat()
            for source, writer in self._writers.items():
                self._session[f"{source}_chunks"] = len(
                    list((self._output_dir / "chunks" / source).glob("*.wav"))
                )
                self._session[f"{source}_first_callback"] = writer.first_callback_monotonic
                self._session[f"{source}_last_callback"] = writer.last_callback_monotonic
                self._session[f"{source}_dropped_blocks"] = writer.dropped_blocks
                self._session[f"{source}_frames"] = writer.total_frames
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
        mic_info = sd.query_devices(mic_device)
        system_info = sd.query_devices(loopback_device)
        mic_rate = int(round(float(mic_info["default_samplerate"]))) or self.sample_rate
        system_rate = int(round(float(system_info["default_samplerate"]))) or self.sample_rate
        self._session = {
            "version": 2,
            "status": "recording",
            "started_at": datetime.now(UTC).isoformat(),
            "channels": self.channels,
            "chunk_seconds": self.chunk_seconds,
            "target_sample_rate": self.target_sample_rate,
            "microphone_sample_rate": mic_rate,
            "system_sample_rate": system_rate,
            "mic_device": mic_device,
            "loopback_device": loopback_device,
            "mic_device_name": str(mic_info["name"]),
            "loopback_device_name": str(system_info["name"]),
        }
        self._write_session()
        self._writers = {
            "microphone": QueuedChunkWriter(
                output_dir / "chunks" / "microphone",
                "mic",
                mic_rate,
                self.channels,
                self.chunk_seconds,
                self.queue_blocks,
                self._write_session,
                lambda value: self._set_level("microphone", value),
            ),
            "system": QueuedChunkWriter(
                output_dir / "chunks" / "system",
                "system",
                system_rate,
                self.channels,
                self.chunk_seconds,
                self.queue_blocks,
                self._write_session,
                lambda value: self._set_level("system", value),
            ),
        }

        def callback(source: str):
            def enqueue(indata, frames, time_info, status):
                if status:
                    logging.warning("Audio callback %s: %s", source, status)
                self._writers[source].enqueue(indata, monotonic())

            return enqueue

        try:
            mic_stream = sd.InputStream(
                device=mic_device,
                samplerate=mic_rate,
                channels=self.channels,
                dtype="float32",
                callback=callback("microphone"),
            )
            system_stream = sd.InputStream(
                device=loopback_device,
                samplerate=system_rate,
                channels=self.channels,
                dtype="float32",
                callback=callback("system"),
            )
            mic_stream.start()
            system_stream.start()
        except Exception:
            for writer in self._writers.values():
                writer.stop()
            self._session["status"] = "failed_to_start"
            self._write_session()
            raise
        self._streams = [mic_stream, system_stream]
        self._active = True

    def stop(self) -> RecordingResult:
        if not self._active or self._output_dir is None or self._session_file is None:
            raise RuntimeError("Активная запись отсутствует")
        for stream in self._streams:
            stream.stop()
            stream.close()
        for writer in self._writers.values():
            writer.stop()
        self._active = False
        self._session["status"] = "recorded"
        self._session["completed_at"] = datetime.now(UTC).isoformat()
        health = self.health
        self._session["health"] = health.__dict__
        self._write_session()
        result = recover_recording(self._output_dir)
        self._session["status"] = "completed"
        self._write_session()
        return result


def _valid_chunks(directory: Path) -> list[Path]:
    import soundfile as sf

    valid: list[Path] = []
    for path in sorted(directory.glob("*.wav")):
        try:
            if sf.info(path).frames > 0:
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


def _resample_linear(data: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or not len(data):
        return data
    size = round(len(data) * target_rate / source_rate)
    source_axis = np.linspace(0.0, 1.0, len(data), endpoint=False)
    target_axis = np.linspace(0.0, 1.0, size, endpoint=False)
    return np.column_stack(
        [np.interp(target_axis, source_axis, data[:, channel]) for channel in range(data.shape[1])]
    )


def mix_tracks(
    microphone: Path,
    system: Path,
    output: Path,
    microphone_rate: int,
    system_rate: int,
    target_rate: int,
    microphone_delay_ms: int,
    system_delay_ms: int,
    microphone_tempo: float = 1.0,
    system_tempo: float = 1.0,
) -> None:
    if shutil.which("ffmpeg"):
        filters = (
            f"[0:a]aresample={target_rate},atempo={microphone_tempo:.8f},"
            f"adelay={microphone_delay_ms}:all=1[m];"
            f"[1:a]aresample={target_rate},atempo={system_tempo:.8f},"
            f"adelay={system_delay_ms}:all=1[s];"
            "[m][s]amix=inputs=2:duration=longest:normalize=0[out]"
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(microphone),
                "-i",
                str(system),
                "-filter_complex",
                filters,
                "-map",
                "[out]",
                "-ar",
                str(target_rate),
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            check=True,
            capture_output=True,
        )
        return
    import soundfile as sf

    mic, _ = sf.read(microphone, always_2d=True)
    sys, _ = sf.read(system, always_2d=True)
    mic = _resample_linear(mic, microphone_rate, target_rate)
    sys = _resample_linear(sys, system_rate, target_rate)
    if microphone_tempo != 1.0:
        mic = _resample_linear(mic, target_rate, round(target_rate / microphone_tempo))
    if system_tempo != 1.0:
        sys = _resample_linear(sys, target_rate, round(target_rate / system_tempo))
    mic = np.pad(mic, ((round(microphone_delay_ms * target_rate / 1000), 0), (0, 0)))
    sys = np.pad(sys, ((round(system_delay_ms * target_rate / 1000), 0), (0, 0)))
    size = max(len(mic), len(sys))
    result = np.zeros((size, max(mic.shape[1], sys.shape[1])))
    result[: len(mic), : mic.shape[1]] += mic
    result[: len(sys), : sys.shape[1]] += sys
    peak = float(np.max(np.abs(result))) or 1.0
    if peak > 1:
        result /= peak
    sf.write(output, result, target_rate, subtype="PCM_16")


def recover_recording(output_dir: Path) -> RecordingResult:
    import soundfile as sf

    session_file = output_dir / "session.json"
    if not session_file.exists():
        raise RuntimeError(f"Манифест записи не найден: {session_file}")
    session = json.loads(session_file.read_text(encoding="utf-8"))
    channels = int(session.get("channels", 1))
    legacy_rate = int(session.get("sample_rate", 48_000))
    mic_rate = int(session.get("microphone_sample_rate", legacy_rate))
    sys_rate = int(session.get("system_sample_rate", legacy_rate))
    target_rate = int(session.get("target_sample_rate", legacy_rate))
    microphone_file = output_dir / "microphone.wav"
    system_file = output_dir / "system.wav"
    mixed_file = output_dir / "lesson.wav"
    sync_report = output_dir / "sync_report.json"
    concatenate_chunks(
        _valid_chunks(output_dir / "chunks" / "microphone"), microphone_file, mic_rate, channels
    )
    concatenate_chunks(_valid_chunks(output_dir / "chunks" / "system"), system_file, sys_rate, channels)
    mic_start = session.get("microphone_first_callback")
    sys_start = session.get("system_first_callback")
    if mic_start is None or sys_start is None:
        mic_start = sys_start = 0.0
    baseline = min(float(mic_start), float(sys_start))
    mic_delay_ms = max(0, round((float(mic_start) - baseline) * 1000))
    sys_delay_ms = max(0, round((float(sys_start) - baseline) * 1000))
    mic_duration = sf.info(microphone_file).duration
    sys_duration = sf.info(system_file).duration
    mic_end_ms = mic_delay_ms + mic_duration * 1000
    sys_end_ms = sys_delay_ms + sys_duration * 1000
    drift_ms = abs(mic_end_ms - sys_end_ms)
    mic_tempo = sys_tempo = 1.0
    drift_correction = False
    if 5 <= drift_ms <= 2000:
        common_end_ms = max(mic_end_ms, sys_end_ms)
        if mic_end_ms < common_end_ms and common_end_ms > mic_delay_ms:
            desired = (common_end_ms - mic_delay_ms) / 1000
            mic_tempo = max(0.5, min(2.0, mic_duration / desired))
            drift_correction = True
        elif sys_end_ms < common_end_ms and common_end_ms > sys_delay_ms:
            desired = (common_end_ms - sys_delay_ms) / 1000
            sys_tempo = max(0.5, min(2.0, sys_duration / desired))
            drift_correction = True
    mix_tracks(
        microphone_file,
        system_file,
        mixed_file,
        mic_rate,
        sys_rate,
        target_rate,
        mic_delay_ms,
        sys_delay_ms,
        mic_tempo,
        sys_tempo,
    )
    report = {
        "microphone_sample_rate": mic_rate,
        "system_sample_rate": sys_rate,
        "target_sample_rate": target_rate,
        "microphone_duration_seconds": round(mic_duration, 4),
        "system_duration_seconds": round(sys_duration, 4),
        "microphone_delay_ms": mic_delay_ms,
        "system_delay_ms": sys_delay_ms,
        "estimated_end_drift_ms": round(drift_ms, 2),
        "drift_correction_applied": drift_correction,
        "microphone_tempo": round(mic_tempo, 8),
        "system_tempo": round(sys_tempo, 8),
        "correction_applied": bool(mic_delay_ms or sys_delay_ms or mic_rate != sys_rate),
        "microphone_dropped_blocks": session.get("microphone_dropped_blocks", 0),
        "system_dropped_blocks": session.get("system_dropped_blocks", 0),
    }
    _atomic_json(sync_report, report)
    return RecordingResult(microphone_file, system_file, mixed_file, session_file, sync_report)


def find_recoverable_recordings(workspace: Path) -> list[Path]:
    sessions: list[Path] = []
    for manifest in workspace.glob("lessons/*/recording/session.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") in {"recording", "recorded"} and any(
            (manifest.parent / "chunks").rglob("*.wav")
        ):
            sessions.append(manifest.parent)
    return sorted(sessions)


def level_to_db(level: float) -> float:
    return 20.0 * math.log10(max(level, 1e-6))
