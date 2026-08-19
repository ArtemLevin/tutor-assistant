from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class TrackQuality:
    path: str
    sample_rate: int
    channels: int
    duration_seconds: float
    peak: float
    rms: float
    silence_ratio: float
    clipped_ratio: float
    dc_offset: float
    ready: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AudioQualityReport:
    ready: bool
    microphone: TrackQuality
    system: TrackQuality
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "microphone": self.microphone.to_dict(),
            "system": self.system.to_dict(),
            "warnings": list(self.warnings),
        }


def analyze_track(path: Path, silence_threshold: float = 0.002) -> TrackQuality:
    import soundfile as sf

    info = sf.info(path)
    frames = 0
    sum_squares = 0.0
    sample_sum = 0.0
    sample_count = 0
    peak = 0.0
    silent_frames = 0
    clipped_frames = 0
    for block in sf.blocks(path, blocksize=65_536, dtype="float32", always_2d=True):
        if not len(block):
            continue
        absolute = np.abs(block)
        frame_peak = np.max(absolute, axis=1)
        frame_rms = np.sqrt(np.mean(np.square(block), axis=1, dtype=np.float64))
        peak = max(peak, float(np.max(frame_peak)))
        silent_frames += int(np.count_nonzero(frame_rms < silence_threshold))
        clipped_frames += int(np.count_nonzero(frame_peak >= 0.98))
        sum_squares += float(np.sum(np.square(block), dtype=np.float64))
        sample_sum += float(np.sum(block, dtype=np.float64))
        sample_count += int(block.size)
        frames += len(block)
    duration = frames / info.samplerate if info.samplerate else 0.0
    rms = float(np.sqrt(sum_squares / sample_count)) if sample_count else 0.0
    silence_ratio = silent_frames / frames if frames else 1.0
    clipped_ratio = clipped_frames / frames if frames else 0.0
    dc_offset = sample_sum / sample_count if sample_count else 0.0
    warnings: list[str] = []
    if duration < 1:
        warnings.append("дорожка короче одной секунды")
    if silence_ratio >= 0.98:
        warnings.append("дорожка практически полностью состоит из тишины")
    elif silence_ratio >= 0.95:
        warnings.append("на дорожке более 95% тишины")
    if clipped_ratio >= 0.01:
        warnings.append("обнаружен заметный клиппинг")
    elif peak >= 0.98:
        warnings.append("обнаружены отдельные перегруженные фрагменты")
    if abs(dc_offset) >= 0.05:
        warnings.append("обнаружено постоянное смещение сигнала")
    ready = duration >= 1 and silence_ratio < 0.98 and clipped_ratio < 0.05
    return TrackQuality(
        path=str(path.resolve()),
        sample_rate=info.samplerate,
        channels=info.channels,
        duration_seconds=round(duration, 4),
        peak=round(peak, 6),
        rms=round(rms, 6),
        silence_ratio=round(silence_ratio, 6),
        clipped_ratio=round(clipped_ratio, 6),
        dc_offset=round(dc_offset, 6),
        ready=ready,
        warnings=tuple(warnings),
    )


def _missing_track(path: Path, label: str) -> TrackQuality:
    return TrackQuality(
        path=str(path.resolve()),
        sample_rate=0,
        channels=0,
        duration_seconds=0.0,
        peak=0.0,
        rms=0.0,
        silence_ratio=1.0,
        clipped_ratio=0.0,
        dc_offset=0.0,
        ready=False,
        warnings=(f"{label} отсутствует; используется единственная доступная дорожка",),
    )


def create_quality_report(microphone: Path, system: Path, output: Path) -> AudioQualityReport:
    microphone_exists = microphone.is_file()
    system_exists = system.is_file()
    if not microphone_exists and not system_exists:
        raise RuntimeError("Невозможно оценить качество: обе аудиодорожки отсутствуют")

    mic = analyze_track(microphone) if microphone_exists else _missing_track(microphone, "дорожка микрофона")
    sys = analyze_track(system) if system_exists else _missing_track(system, "дорожка системного звука")
    warnings = tuple(
        [f"Микрофон: {message}" for message in mic.warnings]
        + [f"Системный звук: {message}" for message in sys.warnings]
    )
    available = [track for track, exists in ((mic, microphone_exists), (sys, system_exists)) if exists]
    report = AudioQualityReport(bool(available) and all(track.ready for track in available), mic, sys, warnings)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    return report
