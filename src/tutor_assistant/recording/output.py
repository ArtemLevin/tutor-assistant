from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal, cast

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


def ensure_output_format_available(value: str) -> None:
    profile = output_profile(value)
    if profile.output_format == "wav":
        return
    _tool_path("ffmpeg")
    _tool_path("ffprobe")


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
        "version": 4,
        "output_format": profile.output_format,
        "output_codec": profile.codec,
        "output_encoder": profile.encoder,
        "output_bitrate_kbps": profile.bitrate_kbps,
    }


def _positive_duration(payload: dict) -> float:
    candidates: list[object] = []
    streams = payload.get("streams")
    if isinstance(streams, list) and streams and isinstance(streams[0], dict):
        candidates.append(streams[0].get("duration"))
    container = payload.get("format")
    if isinstance(container, dict):
        candidates.append(container.get("duration"))
    for value in candidates:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration
    raise RuntimeError("FFprobe не подтвердил положительную длительность итогового аудио")


def _verify_encoded_audio(path: Path, profile: AudioEncodingProfile, ffprobe: str) -> None:
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,duration:format=format_name,duration",
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
        raise RuntimeError("FFprobe не обнаружил аудиопоток в итоговом файле")
    codec = str(streams[0].get("codec_name") or "").casefold()
    if codec != profile.codec:
        raise RuntimeError(
            f"FFprobe обнаружил codec={codec or 'unknown'}, ожидался {profile.codec}"
        )
    _positive_duration(payload)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("Итоговый аудиофайл пуст")


def encode_master_audio(master_file: Path, output_file: Path, value: str) -> Path:
    profile = output_profile(value)
    if profile.output_format == "wav":
        return master_file
    ffmpeg = _tool_path("ffmpeg")
    ffprobe = _tool_path("ffprobe")
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
        _verify_encoded_audio(temporary, profile, ffprobe)
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
    """WAV-first recorder with a configurable verified delivery format."""

    default_output_format: ClassVar[AudioOutputFormat] = "m4a"

    def __init__(self, *args, output_format: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        selected = output_format or type(self).default_output_format
        self.output_format = normalize_output_format(selected)

    @classmethod
    def set_default_output_format(cls, value: str) -> None:
        cls.default_output_format = normalize_output_format(value)

    def start(
        self,
        output_dir: Path,
        mic_device: int,
        system_source: SystemAudioSource | int,
    ) -> None:
        ensure_output_format_available(self.output_format)
        super().start(output_dir, mic_device, system_source)
        profile = output_profile(self.output_format)
        self._session.update(_profile_metadata(profile))
        self._write_session()

    def stop(self) -> RecordingResult:
        wav_result = super().stop()
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
