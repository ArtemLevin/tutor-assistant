from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unicodedata
from dataclasses import replace
from datetime import date
from pathlib import Path

from .atomic_io import atomic_write_text
from .recording.recorder import RecordingResult

_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WHITESPACE = re.compile(r"\s+")
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def readable_audio_stem(student_name: str, lesson_date: date) -> str:
    """Build a Unicode-preserving filename stem safe on Windows."""

    normalized = unicodedata.normalize("NFKC", student_name)
    normalized = _INVALID_WINDOWS_CHARS.sub("_", normalized)
    normalized = _WHITESPACE.sub("_", normalized.strip())
    normalized = re.sub(r"_+", "_", normalized).strip(" ._")
    if not normalized:
        normalized = "Ученик"
    if normalized.upper() in _RESERVED_WINDOWS_NAMES:
        normalized = f"_{normalized}"
    normalized = normalized[:120].rstrip(" ._") or "Ученик"
    return f"{normalized}_{lesson_date.isoformat()}"


def readable_audio_path(source: Path, student_name: str, lesson_date: date) -> Path:
    return source.with_name(f"{readable_audio_stem(student_name, lesson_date)}{source.suffix.lower()}")


def _atomic_copy(source: Path, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.stem}.",
        suffix=destination.suffix,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _update_session_output(session_file: Path, output_file: Path) -> None:
    try:
        payload = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["output_file"] = output_file.name
    payload["readable_output_file"] = output_file.name
    atomic_write_text(
        session_file,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def finalize_readable_audio(
    result: RecordingResult,
    student_name: str,
    lesson_date: date,
) -> RecordingResult:
    """Return a human-readable delivery file while retaining the WAV recovery master."""

    source = result.mixed_file.resolve()
    destination = readable_audio_path(source, student_name, lesson_date).resolve()
    if source == destination:
        _update_session_output(result.session_file, destination)
        return result

    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.casefold() == ".wav" and source.name.casefold() == "lesson.wav":
        _atomic_copy(source, destination)
    else:
        os.replace(source, destination)
    _update_session_output(result.session_file, destination)
    return replace(result, mixed_file=destination)
