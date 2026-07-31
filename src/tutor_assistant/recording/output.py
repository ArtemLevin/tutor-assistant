from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Literal, cast

from ..atomic_io import atomic_write_text
from .devices import SystemAudioSource
from .recorder import (
    DualRecorder as WavDualRecorder,
)
from .recorder import (
    RecordingResult,
)
from .recorder import (
    recover_recording as recover_wav_recording,
)

AudioOutputFormat = Literal["m4a", "mp3", "wav"]


@dataclass(frozen=True)
class AudioEncodingProfile:
    output_format: AudioOutputFormat
    suffix: str
    codec: str
    encoder: str
    bitrate_kbps: int | None
    ffmpeg_arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class AudioProbe:
    codec: str
    duration_seconds: float
    sample_rate_hz: int
    channels: int
    bitrate_bps: int | None


AUDIO_ENCODING_PROFILES: dict[AudioOutputFormat, AudioEncodingProfile] = {
    "m4a": AudioEncodingProfile(
        output_format="m4a",
        suffix=".m4a",
        codec="aac",
        encoder="aac",
        bitrate_kbps=96,
        ffmpeg_arguments=("-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart"),
    ),
    "mp3": AudioEncodingProfile(
        output_format="mp3",
        suffix=".mp3",
        codec="mp3",
        encoder="libmp3lame",
        bitrate_kbps=128,
        ffmpeg_arguments=("-c:a", "libmp3lame", "-b:a", "128k"),
    ),
    "wav": AudioEncodingProfile(
        output_format="wav",
        suffix=".wav",
        codec="pcm_s16le",
        encoder="pcm_s16le",
        bitrate_kbps=None,
    ),
}


def normalize_output_format(value: str) -> AudioOutputFormat:
    normalized = value.strip().casefold()
    if normalized not in AUDIO_ENCODING_PROFILES:
        supported = ", ".join(AUDIO_ENCODING_PROFILES)
        raise ValueError(f"Неподдерживаемый формат аудио: {value}. Доступны: {supported}")
    return cast(AudioOutputFormat, normalized)


def output_profile(value: str) -> AudioEncodingProfile:
    return AUDIO_ENCODING_PROFILES[normalize_output_format(value)]


def _tool_path(name: str) -> str:
    executable = shutil.which(name)
    if executable:
        return executable
    raise RuntimeError(
        f"Для M4A и MP3 требуется {name}. Установите полный комплект FFmpeg и повторите проверку."
    )


@lru_cache(maxsize=16)
def _ensure_encoder_available(ffmpeg: str, encoder: str) -> None:
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"FFmpeg не смог перечислить кодировщики: {details[-1200:]}") from exc
    pattern = rf"(?m)^\s*[A-Z.]+\s+{re.escape(encoder)}(?:\s|$)"
    if re.search(pattern, completed.stdout) is None:
        raise RuntimeError(
            f"Установленный FFmpeg не содержит кодировщик {encoder}. "
            "Установите полную сборку FFmpeg."
        )


def ensure_output_format_available(value: str) -> None:
    profile = output_profile(value)
    if profile.output_format == "wav":
        return
    ffmpeg = _tool_path("ffmpeg")
    _tool_path("ffprobe")
    _ensure_encoder_available(ffmpeg, profile.encoder)


def _read_session(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_session(path: Path, payload: dict) -> None:
    payload["updated_at"] = datetime.now(UTC).isoformat()
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _profile_metadata(profile: AudioEncodingProfile) -> dict:
    return {
        "version": 5,
        "output_format": profile.output_format,
        "output_codec": profile.codec,
        "output_encoder": profile.encoder,
        "output_bitrate_kbps": profile.bitrate_kbps,
    }


def _positive_float(value: object, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"FFprobe не вернул корректное поле {field}") from exc
    if parsed <= 0:
        raise RuntimeError(f"FFprobe вернул неположительное поле {field}")
    return parsed


def _positive_int(value: object, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"FFprobe не вернул корректное поле {field}") from exc
    if parsed <= 0:
        raise RuntimeError(f"FFprobe вернул неположительное поле {field}")
    return parsed


def _optional_positive_int(*values: object) -> int | None:
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _probe_audio(path: Path, ffprobe: str) -> AudioProbe:
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,duration,sample_rate,channels,bit_rate:format=duration,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("FFprobe вернул повреждённый JSON") from exc
    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise RuntimeError("FFprobe не обнаружил аудиопоток")
    stream = streams[0]
    container = payload.get("format")
    container = container if isinstance(container, dict) else {}
    duration_value = stream.get("duration") or container.get("duration")
    return AudioProbe(
        codec=str(stream.get("codec_name") or "").casefold(),
        duration_seconds=_positive_float(duration_value, "duration"),
        sample_rate_hz=_positive_int(stream.get("sample_rate"), "sample_rate"),
        channels=_positive_int(stream.get("channels"), "channels"),
        bitrate_bps=_optional_positive_int(stream.get("bit_rate"), container.get("bit_rate")),
    )


def _verify_encoded_audio(
    path: Path,
    profile: AudioEncodingProfile,
    ffprobe: str,
    master: AudioProbe,
) -> AudioProbe:
    encoded = _probe_audio(path, ffprobe)
    if encoded.codec != profile.codec:
        raise RuntimeError(
            f"FFprobe обнаружил codec={encoded.codec or 'unknown'}, ожидался {profile.codec}"
        )
    duration_tolerance = max(1.0, master.duration_seconds * 0.02)
    duration_delta = abs(encoded.duration_seconds - master.duration_seconds)
    if duration_delta > duration_tolerance:
        raise RuntimeError(
            "Длительность итогового аудио отличается от WAV-мастера: "
            f"master={master.duration_seconds:.3f}s, output={encoded.duration_seconds:.3f}s"
        )
    if encoded.sample_rate_hz != master.sample_rate_hz:
        raise RuntimeError(
            "Частота дискретизации итогового аудио изменилась: "
            f"master={master.sample_rate_hz}, output={encoded.sample_rate_hz}"
        )
    if encoded.channels != master.channels:
        raise RuntimeError(
            "Количество каналов итогового аудио изменилось: "
            f"master={master.channels}, output={encoded.channels}"
        )
    if profile.bitrate_kbps is not None and encoded.bitrate_bps is not None:
        target = profile.bitrate_kbps * 1000
        if not target * 0.65 <= encoded.bitrate_bps <= target * 1.45:
            raise RuntimeError(
                "Битрейт итогового аудио выходит за допустимый диапазон: "
                f"target={target}, actual={encoded.bitrate_bps}"
            )
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("Итоговый аудиофайл пуст")
    return encoded


def encode_master_audio(master_file: Path, output_file: Path, value: str) -> Path:
    profile = output_profile(value)
    if profile.output_format == "wav":
        return master_file
    ensure_output_format_available(profile.output_format)
    ffmpeg = _tool_path("ffmpeg")
    ffprobe = _tool_path("ffprobe")
    master_probe = _probe_audio(master_file, ffprobe)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_file.parent,
        prefix=f".{output_file.stem}.",
        suffix=output_file.suffix,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(master_file),
                "-map_metadata",
                "-1",
                "-vn",
                *profile.ffmpeg_arguments,
                str(temporary),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
        )
        _verify_encoded_audio(temporary, profile, ffprobe, master_probe)
        os.replace(temporary, output_file)
        return output_file
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"FFmpeg завершил кодирование с ошибкой: {details[-1200:]}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _mark_encoding_failure(
    session_file: Path,
    master_file: Path,
    profile: AudioEncodingProfile,
    error: Exception,
) -> None:
    session = _read_session(session_file)
    session.update(_profile_metadata(profile))
    session.update(
        {
            "status": "encoding_failed",
            "master_file": master_file.name,
            "encoding_error": str(error),
        }
    )
    _write_session(session_file, session)


def finalize_recording_output(
    result: RecordingResult,
    value: str,
) -> RecordingResult:
    profile = output_profile(value)
    master_file = result.mixed_file
    output_file = (
        master_file
        if profile.output_format == "wav"
        else master_file.with_name(f"lesson{profile.suffix}")
    )
    try:
        final_file = encode_master_audio(master_file, output_file, profile.output_format)
    except Exception as exc:
        _mark_encoding_failure(result.session_file, master_file, profile, exc)
        raise RuntimeError(
            f"Не удалось создать {profile.output_format.upper()}. "
            f"WAV-мастер сохранён: {master_file}"
        ) from exc
    session = _read_session(result.session_file)
    session.update(_profile_metadata(profile))
    session.update(
        {
            "status": "completed",
            "master_file": master_file.name,
            "output_file": final_file.name,
            "encoding_completed_at": datetime.now(UTC).isoformat(),
        }
    )
    session.pop("encoding_error", None)
    _write_session(result.session_file, session)
    return replace(result, mixed_file=final_file)


class DualRecorder(WavDualRecorder):
    """WAV-first recorder with an explicit verified delivery format."""

    def __init__(self, *args, output_format: str = "m4a", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.output_format = normalize_output_format(output_format)
        self._output_started_monotonic: float | None = None

    @property
    def health(self):
        health = super().health
        if not self.active or self._output_started_monotonic is None:
            return health
        elapsed = max(0.0, monotonic() - self._output_started_monotonic)
        microphone = self._writers.get("microphone")
        system = self._writers.get("system")
        microphone_age = health.microphone_callback_age_seconds
        system_age = health.system_callback_age_seconds
        if microphone is not None and microphone.last_callback_monotonic is None:
            microphone_age = round(elapsed, 2)
        if system is not None and system.last_callback_monotonic is None:
            system_age = round(elapsed, 2)
        return replace(
            health,
            microphone_callback_age_seconds=microphone_age,
            system_callback_age_seconds=system_age,
        )

    def start(
        self,
        output_dir: Path,
        mic_device: int,
        system_source: SystemAudioSource | int,
    ) -> None:
        ensure_output_format_available(self.output_format)
        self._output_started_monotonic = monotonic()
        try:
            super().start(output_dir, mic_device, system_source)
        except Exception:
            self._output_started_monotonic = None
            raise
        profile = output_profile(self.output_format)
        self._session.update(_profile_metadata(profile))
        self._write_session()

    def stop(self) -> RecordingResult:
        wav_result = super().stop()
        self._output_started_monotonic = None
        return finalize_recording_output(wav_result, self.output_format)


def recover_recording(
    output_dir: Path,
    output_format: str | None = None,
) -> RecordingResult:
    session_file = output_dir / "session.json"
    session = _read_session(session_file)
    selected = output_format or str(session.get("output_format") or "wav")
    wav_result = recover_wav_recording(output_dir)
    return finalize_recording_output(wav_result, selected)
