from __future__ import annotations

import json
import subprocess
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import tutor_assistant.recording as recording_package
import tutor_assistant.recording.recorder as recorder_module
from tutor_assistant.config import AppConfig, RecordingConfig
from tutor_assistant.recording import output as output_module
from tutor_assistant.recording.output import (
    DualRecorder,
    encode_master_audio,
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


def _probe_payload(
    codec: str,
    *,
    duration: float = 12.5,
    sample_rate: int = 48_000,
    channels: int = 1,
    bitrate: int | None = 96_000,
) -> str:
    stream: dict[str, object] = {
        "codec_name": codec,
        "duration": str(duration),
        "sample_rate": str(sample_rate),
        "channels": channels,
    }
    container: dict[str, object] = {"duration": str(duration)}
    if bitrate is not None:
        stream["bit_rate"] = str(bitrate)
        container["bit_rate"] = str(bitrate)
    return json.dumps({"streams": [stream], "format": container})


def _encoder_output(*encoders: str) -> str:
    return "\n".join(f" A..... {encoder} test encoder" for encoder in encoders)


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


def test_capability_probe_requires_selected_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_module._ensure_encoder_available.cache_clear()
    monkeypatch.setattr(output_module.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        output_module.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=_encoder_output("aac"),
            stderr="",
        ),
    )

    ensure_output_format_available("m4a")
    with pytest.raises(RuntimeError, match="libmp3lame"):
        ensure_output_format_available("mp3")


def test_m4a_low_reported_bitrate_is_verified_and_committed_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    commands: list[list[str]] = []
    output_module._ensure_encoder_available.cache_clear()

    def fake_which(name: str) -> str:
        return f"C:/ffmpeg/{name}.exe"

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert kwargs["check"] is True
        if "-encoders" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_encoder_output("aac"),
                stderr="",
            )
        if command[0].endswith("ffmpeg.exe"):
            Path(command[-1]).write_bytes(b"encoded-m4a")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        codec = "pcm_s16le" if Path(command[-1]) == result.mixed_file else "aac"
        bitrate = 768_000 if codec == "pcm_s16le" else 56_242
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_probe_payload(codec, bitrate=bitrate),
            stderr="",
        )

    monkeypatch.setattr(output_module.shutil, "which", fake_which)
    monkeypatch.setattr(output_module.subprocess, "run", fake_run)

    finalized = finalize_recording_output(result, "m4a")

    assert finalized.mixed_file == tmp_path / "lesson.m4a"
    assert finalized.mixed_file.read_bytes() == b"encoded-m4a"
    assert result.mixed_file.exists()
    assert any("96k" in command for command in commands)
    assert sum("-show_entries" in command for command in commands) == 2
    session = json.loads(result.session_file.read_text(encoding="utf-8"))
    assert session["version"] == 6
    assert session["output_format"] == "m4a"
    assert session["output_codec"] == "aac"
    assert session["output_bitrate_kbps"] == 96
    assert session["actual_output_bitrate_bps"] == 56_242
    assert session["output_sample_rate_hz"] == 48_000
    assert session["output_channels"] == 1
    assert session["master_file"] == "lesson.wav"
    assert session["output_file"] == "lesson.m4a"
    assert session["status"] == "completed"


def test_missing_reported_bitrate_is_not_an_integrity_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = tmp_path / "lesson.m4a"
    encoded.write_bytes(b"encoded")
    master_probe = output_module.AudioProbe("pcm_s16le", 12.5, 48_000, 1, 768_000)
    encoded_probe = output_module.AudioProbe("aac", 12.5, 48_000, 1, None)
    monkeypatch.setattr(output_module, "_probe_audio", lambda _path, _ffprobe: encoded_probe)

    verified = output_module._verify_encoded_audio(
        encoded,
        output_module.output_profile("m4a"),
        "ffprobe",
        master_probe,
    )

    assert verified.bitrate_bps is None


def test_mp3_uses_expected_encoder_and_bitrate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    ffmpeg_command: list[str] = []
    output_module._ensure_encoder_available.cache_clear()
    monkeypatch.setattr(output_module.shutil, "which", lambda name: name)

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        if "-encoders" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=_encoder_output("libmp3lame"),
                stderr="",
            )
        if command[0] == "ffmpeg":
            ffmpeg_command.extend(command)
            Path(command[-1]).write_bytes(b"encoded-mp3")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        codec = "pcm_s16le" if Path(command[-1]) == result.mixed_file else "mp3"
        bitrate = 768_000 if codec == "pcm_s16le" else 128_000
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_probe_payload(codec, duration=1.0, bitrate=bitrate),
            stderr="",
        )

    monkeypatch.setattr(output_module.subprocess, "run", fake_run)

    finalized = finalize_recording_output(result, "mp3")

    assert finalized.mixed_file.suffix == ".mp3"
    assert "libmp3lame" in ffmpeg_command
    assert "128k" in ffmpeg_command


def test_duration_mismatch_rejects_truncated_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    output_module._ensure_encoder_available.cache_clear()
    monkeypatch.setattr(output_module.shutil, "which", lambda name: name)

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        if "-encoders" in command:
            return subprocess.CompletedProcess(command, 0, stdout=_encoder_output("aac"), stderr="")
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"truncated")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if Path(command[-1]) == result.mixed_file:
            payload = _probe_payload("pcm_s16le", duration=120.0, bitrate=768_000)
        else:
            payload = _probe_payload("aac", duration=2.0, bitrate=96_000)
        return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")

    monkeypatch.setattr(output_module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Длительность"):
        encode_master_audio(result.mixed_file, tmp_path / "lesson.m4a", "m4a")


def test_sample_rate_mismatch_rejects_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    output_module._ensure_encoder_available.cache_clear()
    monkeypatch.setattr(output_module.shutil, "which", lambda name: name)

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        if "-encoders" in command:
            return subprocess.CompletedProcess(command, 0, stdout=_encoder_output("aac"), stderr="")
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"resampled")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if Path(command[-1]) == result.mixed_file:
            payload = _probe_payload("pcm_s16le", sample_rate=48_000, bitrate=768_000)
        else:
            payload = _probe_payload("aac", sample_rate=44_100, bitrate=96_000)
        return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")

    monkeypatch.setattr(output_module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Частота дискретизации"):
        encode_master_audio(result.mixed_file, tmp_path / "lesson.m4a", "m4a")


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


def test_non_wav_master_is_rejected_before_delivery_reencoding(tmp_path: Path) -> None:
    result = _recording_result(tmp_path)
    encoded_master = tmp_path / "lesson.m4a"
    encoded_master.write_bytes(b"already-encoded")
    result = RecordingResult(
        result.microphone_file,
        result.system_file,
        encoded_master,
        result.session_file,
        result.sync_report,
        result.quality_report,
    )

    with pytest.raises(RuntimeError, match="требуется WAV-мастер"):
        finalize_recording_output(result, "m4a")


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


def test_encoding_failed_recovery_recreates_selected_delivery_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    result.session_file.write_text(
        json.dumps(
            {
                "status": "encoding_failed",
                "output_format": "m4a",
                "encoding_error": "old failure",
            }
        ),
        encoding="utf-8",
    )
    output_file = tmp_path / "lesson.m4a"
    probe = output_module.AudioProbe("aac", 12.5, 48_000, 1, 56_242)
    monkeypatch.setattr(output_module, "recover_wav_recording", lambda _path: result)

    def fake_encode(master: Path, output: Path, profile):
        assert master == result.mixed_file
        assert profile.output_format == "m4a"
        output.write_bytes(b"recovered-m4a")
        return output, probe

    monkeypatch.setattr(output_module, "_encode_master_audio_with_probe", fake_encode)

    recovered = recover_recording(tmp_path)

    assert recovered.mixed_file == output_file
    assert output_file.read_bytes() == b"recovered-m4a"
    session = json.loads(result.session_file.read_text(encoding="utf-8"))
    assert session["status"] == "completed"
    assert session["master_file"] == "lesson.wav"
    assert session["output_file"] == "lesson.m4a"
    assert session["actual_output_bitrate_bps"] == 56_242
    assert "encoding_error" not in session


def test_package_recovery_uses_session_output_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    result.session_file.write_text('{"output_format": "mp3"}', encoding="utf-8")
    selected: list[str] = []
    monkeypatch.setattr(output_module, "recover_wav_recording", lambda _path: result)
    monkeypatch.setattr(
        output_module,
        "finalize_recording_output",
        lambda value, output_format: selected.append(output_format) or value,
    )

    assert recording_package.recover_recording(tmp_path) is result
    assert selected == ["mp3"]


def test_package_import_keeps_wav_recovery_isolated() -> None:
    assert recorder_module.recover_recording is recorder_module.recover_wav_recording
    assert recording_package.recover_wav_recording is recorder_module.recover_wav_recording
    assert recording_package.recover_recording is output_module.recover_recording
    assert recorder_module.recover_recording is not output_module.recover_recording


def test_output_recorder_stop_finalizes_once_from_wav(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    selected: list[tuple[Path, str]] = []
    recorder = DualRecorder(output_format="m4a")
    recorder._active = True
    recorder._output_dir = tmp_path
    recorder._session_file = result.session_file
    recorder._session = {}
    recorder._streams = []
    recorder._writers = {}
    monkeypatch.setattr(recorder_module, "recover_wav_recording", lambda _path: result)
    monkeypatch.setattr(
        output_module,
        "finalize_recording_output",
        lambda value, output_format: selected.append((value.mixed_file, output_format)) or value,
    )

    assert recorder.stop() is result
    assert selected == [(tmp_path / "lesson.wav", "m4a")]


def test_recorder_formats_are_instance_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _recording_result(tmp_path)
    result.session_file.write_text('{"status": "completed"}', encoding="utf-8")
    selected: list[str] = []
    monkeypatch.setattr(output_module.WavDualRecorder, "stop", lambda _self: result)
    monkeypatch.setattr(
        output_module,
        "finalize_recording_output",
        lambda value, output_format: selected.append(output_format) or value,
    )

    first = DualRecorder(output_format="mp3")
    second = DualRecorder(output_format="wav")

    assert first.stop() is result
    assert second.stop() is result
    assert selected == ["mp3", "wav"]


def test_health_counts_time_before_first_callback() -> None:
    recorder = DualRecorder(output_format="wav")
    writer = SimpleNamespace(
        queue_percent=0,
        dropped_blocks=0,
        max_latency_seconds=0.0,
        first_callback_monotonic=None,
        last_callback_monotonic=None,
        last_non_silent_monotonic=None,
    )
    recorder._writers = {"microphone": writer, "system": writer}
    recorder._active = True
    recorder._output_started_monotonic = monotonic() - 6

    health = recorder.health

    assert health.microphone_callback_age_seconds >= 5.5
    assert health.system_callback_age_seconds >= 5.5


def test_production_gui_uses_instance_recorder_factory() -> None:
    source = Path(
        "src/tutor_assistant/ui/transcript_publication_app.py"
    ).read_text(encoding="utf-8")

    assert 'setObjectName("audioOutputFormat")' in source
    assert 'self._lesson_form().addRow("Итоговый формат аудио"' in source
    assert "_create_configured_recorder" in source
    assert 'kwargs["output_format"] = self.config.recording.output_format' in source
    assert "set_default_output_format" not in source
