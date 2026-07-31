from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from tutor_assistant.config import AppConfig, RecordingConfig
from tutor_assistant.recording import output as output_module
from tutor_assistant.recording.output import (
    DualRecorder,
    ensure_output_format_available,
    finalize_recording_output,
    normalize_output_format,
    recover_recording,
)
from tutor_assistant.recording.recorder import RecordingResult


def _recording_result(tmp_path: Path) -> RecordingResult:
    microphone = tmp_path / "microphone.wav"
    system = tmp_path / "system.wav"
    master = tmp_path / "lesson.wav"
    session = tmp_path / "session.json"
    sync_report = tmp_path / "sync_report.json"
    quality_report = tmp_path / "audio_quality_report.json"
    for path in (microphone, system, master):
        path.write_bytes(b"RIFF-test-audio")
    session.write_text("{}", encoding="utf-8")
    sync_report.write_text("{}", encoding="utf-8")
    quality_report.write_text("{}", encoding="utf-8")
    return RecordingResult(
        microphone_file=microphone,
        system_file=system,
        mixed_file=master,
        session_file=session,
        sync_report=sync_report,
        quality_report=quality_report,
    )


def test_recording_config_defaults_to_m4a_and_round_trips(tmp_path: Path) -> None:
    assert RecordingConfig().output_format == "m4a"
    config = AppConfig()
    config.recording.output_format = "mp3"
    path = tmp_path / "app.yaml"

    config.save(path)

    assert AppConfig.load(path).recording.output_format == "mp3"


def test_recording_config_rejects_unknown_format() -> None:
    with pytest.raises(ValidationError):
        RecordingConfig(output_format="ogg")


def test_format_normalization_is_strict() -> None:
    assert normalize_output_format(" M4A ") == "m4a"
    with pytest.raises(ValueError, match="Неподдерживаемый формат"):
        normalize_output_format("flac")


def test_wav_requires_no_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        output_module.shutil,
        "which",
        lambda name: pytest.fail(f"which({name}) не должен вызываться для WAV"),
    )

    ensure_output_format_available("wav")


def test_compressed_format_requires_ffmpeg_and_ffprobe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(output_module.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="FFmpeg"):
        ensure_output_format_available("m4a")


def test_m4a_encoding_is_verified_and_committed_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    commands: list[list[str]] = []

    def fake_which(name: str) -> str:
        return f"C:/ffmpeg/{name}.exe"

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert kwargs["check"] is True
        if command[0].endswith("ffmpeg.exe"):
            Path(command[-1]).write_bytes(b"encoded-m4a")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        payload = {
            "streams": [{"codec_name": "aac", "duration": "12.5"}],
            "format": {"format_name": "mov,mp4,m4a", "duration": "12.5"},
        }
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(output_module.shutil, "which", fake_which)
    monkeypatch.setattr(output_module.subprocess, "run", fake_run)

    finalized = finalize_recording_output(result, "m4a")

    assert finalized.mixed_file == tmp_path / "lesson.m4a"
    assert finalized.mixed_file.read_bytes() == b"encoded-m4a"
    assert result.mixed_file.exists()
    assert any("96k" in command for command in commands)
    session = json.loads(result.session_file.read_text(encoding="utf-8"))
    assert session["version"] == 4
    assert session["output_format"] == "m4a"
    assert session["output_codec"] == "aac"
    assert session["output_bitrate_kbps"] == 96
    assert session["master_file"] == "lesson.wav"
    assert session["output_file"] == "lesson.m4a"
    assert session["status"] == "completed"


def test_mp3_uses_expected_encoder_and_bitrate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    ffmpeg_command: list[str] = []

    monkeypatch.setattr(output_module.shutil, "which", lambda name: name)

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        if command[0] == "ffmpeg":
            ffmpeg_command.extend(command)
            Path(command[-1]).write_bytes(b"encoded-mp3")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "streams": [{"codec_name": "mp3", "duration": "1.0"}],
                    "format": {"format_name": "mp3", "duration": "1.0"},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(output_module.subprocess, "run", fake_run)

    finalized = finalize_recording_output(result, "mp3")

    assert finalized.mixed_file.suffix == ".mp3"
    assert "libmp3lame" in ffmpeg_command
    assert "128k" in ffmpeg_command


def test_encoding_failure_preserves_master_and_marks_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    monkeypatch.setattr(output_module.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="WAV-мастер сохранён"):
        finalize_recording_output(result, "m4a")

    assert result.mixed_file.exists()
    session = json.loads(result.session_file.read_text(encoding="utf-8"))
    assert session["status"] == "encoding_failed"
    assert session["output_format"] == "m4a"
    assert session["master_file"] == "lesson.wav"
    assert "encoding_error" in session


def test_legacy_recovery_defaults_to_wav(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    result.session_file.write_text('{"version": 3}', encoding="utf-8")
    monkeypatch.setattr(output_module, "recover_wav_recording", lambda _path: result)

    recovered = recover_recording(tmp_path)

    assert recovered.mixed_file == result.mixed_file
    session = json.loads(result.session_file.read_text(encoding="utf-8"))
    assert session["output_format"] == "wav"
    assert session["output_codec"] == "pcm_s16le"


def test_recorder_default_format_is_configurable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    selected: list[str] = []
    monkeypatch.setattr(
        output_module.WavDualRecorder,
        "stop",
        lambda _self: result,
    )
    monkeypatch.setattr(
        output_module,
        "finalize_recording_output",
        lambda value, output_format: selected.append(output_format) or value,
    )
    DualRecorder.set_default_output_format("mp3")
    recorder = DualRecorder()

    assert recorder.stop() is result
    assert selected == ["mp3"]


def test_production_gui_exposes_detailed_format_selector() -> None:
    source = Path(
        "src/tutor_assistant/ui/transcript_publication_app.py"
    ).read_text(encoding="utf-8")

    assert 'setObjectName("audioOutputFormat")' in source
    assert 'form.addRow("Итоговый формат аудио"' in source
    assert "DualRecorder.set_default_output_format" in source
