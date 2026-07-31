from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from tutor_assistant.audio_files import finalize_readable_audio, readable_audio_stem
from tutor_assistant.recording.recorder import RecordingResult


def _result(tmp_path: Path, suffix: str) -> RecordingResult:
    microphone = tmp_path / "microphone.wav"
    system = tmp_path / "system.wav"
    mixed = tmp_path / f"lesson{suffix}"
    session = tmp_path / "session.json"
    sync_report = tmp_path / "sync_report.json"
    quality_report = tmp_path / "audio_quality_report.json"
    for path in (microphone, system, mixed):
        path.write_bytes(b"audio")
    session.write_text(
        json.dumps(
            {
                "master_file": "lesson.wav",
                "output_file": mixed.name,
            }
        ),
        encoding="utf-8",
    )
    sync_report.write_text("{}", encoding="utf-8")
    quality_report.write_text("{}", encoding="utf-8")
    return RecordingResult(
        microphone_file=microphone,
        system_file=system,
        mixed_file=mixed,
        session_file=session,
        sync_report=sync_report,
        quality_report=quality_report,
    )


def test_readable_audio_stem_preserves_cyrillic() -> None:
    assert readable_audio_stem("Иван Петров", date(2026, 7, 31)) == (
        "Иван_Петров_2026-07-31"
    )


def test_readable_audio_stem_removes_windows_unsafe_characters() -> None:
    assert readable_audio_stem('  Анна: Петрова / 9Б  ', date(2026, 7, 31)) == (
        "Анна_Петрова_9Б_2026-07-31"
    )


def test_compressed_delivery_is_atomically_renamed(tmp_path: Path) -> None:
    result = _result(tmp_path, ".m4a")
    master = tmp_path / "lesson.wav"
    master.write_bytes(b"master")

    readable = finalize_readable_audio(
        result,
        "Иван Петров",
        date(2026, 7, 31),
    )

    assert readable.mixed_file.name == "Иван_Петров_2026-07-31.m4a"
    assert readable.mixed_file.read_bytes() == b"audio"
    assert not result.mixed_file.exists()
    assert master.read_bytes() == b"master"
    session = json.loads(result.session_file.read_text(encoding="utf-8"))
    assert session["master_file"] == "lesson.wav"
    assert session["output_file"] == readable.mixed_file.name
    assert session["readable_output_file"] == readable.mixed_file.name


def test_wav_delivery_keeps_recovery_master(tmp_path: Path) -> None:
    result = _result(tmp_path, ".wav")

    readable = finalize_readable_audio(
        result,
        "Мария Соколова",
        date(2026, 7, 30),
    )

    assert result.mixed_file.name == "lesson.wav"
    assert result.mixed_file.read_bytes() == b"audio"
    assert readable.mixed_file.name == "Мария_Соколова_2026-07-30.wav"
    assert readable.mixed_file.read_bytes() == b"audio"
    session = json.loads(result.session_file.read_text(encoding="utf-8"))
    assert session["master_file"] == "lesson.wav"
    assert session["output_file"] == readable.mixed_file.name
