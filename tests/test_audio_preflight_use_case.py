from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tutor_assistant.application.audio_preflight import AudioPreflightUseCase


class FakeRecorder:
    def __init__(
        self,
        recording,
        events: list[str],
        *,
        start_error: BaseException | None = None,
        stop_error: BaseException | None = None,
    ) -> None:
        self.recording = recording
        self.events = events
        self.start_error = start_error
        self.stop_error = stop_error
        self._active = False
        self.stop_calls = 0
        self.start_args: tuple[Path, int, object] | None = None

    @property
    def active(self) -> bool:
        return self._active

    def start(self, output_dir: Path, mic_device: int, system_source: object) -> None:
        self.start_args = (output_dir, mic_device, system_source)
        self.events.append("recorder.start")
        self._active = True
        if self.start_error is not None:
            raise self.start_error

    def stop(self):
        self.stop_calls += 1
        self.events.append("recorder.stop")
        if self.stop_error is not None:
            raise self.stop_error
        self._active = False
        return self.recording


def _recording(tmp_path: Path, *, quality: object) -> SimpleNamespace:
    directory = tmp_path / "result"
    directory.mkdir()
    microphone = directory / "microphone.wav"
    system = directory / "system.wav"
    report = directory / "quality.json"
    microphone.write_bytes(b"mic")
    system.write_bytes(b"system")
    if isinstance(quality, str):
        report.write_text(quality, encoding="utf-8")
    else:
        report.write_text(json.dumps(quality), encoding="utf-8")
    return SimpleNamespace(
        microphone_file=microphone,
        system_file=system,
        quality_report=report,
    )


def test_preflight_capture_returns_typed_quality_result(tmp_path: Path) -> None:
    events: list[str] = []
    recording = _recording(
        tmp_path,
        quality={
            "microphone": {"rms": 0.12},
            "system": {"rms": 0.08},
            "warnings": ["system low"],
            "ready": False,
        },
    )
    recorder = FakeRecorder(recording, events)
    observed_chunk_seconds: list[int] = []

    def factory(chunk_seconds: int) -> FakeRecorder:
        observed_chunk_seconds.append(chunk_seconds)
        events.append("recorder.create")
        return recorder

    def sleeper(seconds: float) -> None:
        events.append(f"sleep:{seconds}")

    use_case = AudioPreflightUseCase(
        factory,
        sleeper=sleeper,
        clock=lambda: datetime(2026, 8, 17, 20, 30, 45),
    )
    source = object()

    result = use_case.run(tmp_path, 7, source, 3.0, 2)

    assert observed_chunk_seconds == [4]
    assert recorder.start_args == (
        tmp_path / "diagnostics" / "20260817-203045",
        7,
        source,
    )
    assert events == ["recorder.create", "recorder.start", "sleep:3.0", "recorder.stop"]
    assert recorder.stop_calls == 1
    assert not recorder.active
    assert result.ready is False
    assert result.microphone_rms == pytest.approx(0.12)
    assert result.system_rms == pytest.approx(0.08)
    assert result.warnings == ("system low",)
    assert result.microphone_file == recording.microphone_file
    assert result.system_file == recording.system_file
    assert result.quality_report == recording.quality_report


def test_start_failure_stops_partially_active_diagnostic_recorder(tmp_path: Path) -> None:
    events: list[str] = []
    recording = _recording(
        tmp_path,
        quality={"microphone": {"rms": 0.1}, "system": {"rms": 0.1}, "ready": True},
    )
    recorder = FakeRecorder(recording, events, start_error=RuntimeError("start boom"))
    use_case = AudioPreflightUseCase(lambda _chunk: recorder, sleeper=lambda _seconds: None)

    with pytest.raises(RuntimeError, match="start boom"):
        use_case.run(tmp_path, 1, object(), 1.0, 1)

    assert recorder.stop_calls == 1
    assert not recorder.active
    assert events == ["recorder.start", "recorder.stop"]


def test_sleep_failure_stops_active_diagnostic_recorder(tmp_path: Path) -> None:
    events: list[str] = []
    recording = _recording(
        tmp_path,
        quality={"microphone": {"rms": 0.1}, "system": {"rms": 0.1}, "ready": True},
    )
    recorder = FakeRecorder(recording, events)

    def fail_sleep(_seconds: float) -> None:
        raise RuntimeError("sleep boom")

    use_case = AudioPreflightUseCase(lambda _chunk: recorder, sleeper=fail_sleep)

    with pytest.raises(RuntimeError, match="sleep boom"):
        use_case.run(tmp_path, 1, object(), 1.0, 1)

    assert recorder.stop_calls == 1
    assert not recorder.active


def test_stop_failure_is_not_retried_implicitly(tmp_path: Path) -> None:
    events: list[str] = []
    recording = _recording(
        tmp_path,
        quality={"microphone": {"rms": 0.1}, "system": {"rms": 0.1}, "ready": True},
    )
    recorder = FakeRecorder(recording, events, stop_error=RuntimeError("stop boom"))
    use_case = AudioPreflightUseCase(lambda _chunk: recorder, sleeper=lambda _seconds: None)

    with pytest.raises(RuntimeError, match="stop boom"):
        use_case.run(tmp_path, 1, object(), 1.0, 1)

    assert recorder.stop_calls == 1


def test_malformed_quality_report_is_reported_after_capture(tmp_path: Path) -> None:
    events: list[str] = []
    recording = _recording(tmp_path, quality="{broken")
    recorder = FakeRecorder(recording, events)
    use_case = AudioPreflightUseCase(lambda _chunk: recorder, sleeper=lambda _seconds: None)

    with pytest.raises(RuntimeError, match="Не удалось прочитать отчёт аудиодиагностики"):
        use_case.run(tmp_path, 1, object(), 1.0, 1)

    assert recorder.stop_calls == 1
    assert not recorder.active


def test_invalid_duration_rejects_before_recorder_construction(tmp_path: Path) -> None:
    constructed = False

    def factory(_chunk_seconds: int):
        nonlocal constructed
        constructed = True
        raise AssertionError("factory must not be called")

    use_case = AudioPreflightUseCase(factory)

    with pytest.raises(ValueError, match="положительной"):
        use_case.run(tmp_path, 1, object(), 0, 1)

    assert not constructed


def test_application_preflight_layer_remains_qt_free() -> None:
    source = Path("src/tutor_assistant/application/audio_preflight.py").read_text(encoding="utf-8")

    assert "PySide" not in source
    assert "QThread" not in source
    assert "QMessageBox" not in source


def test_production_preflight_routes_through_use_case_not_legacy_super() -> None:
    source = Path("src/tutor_assistant/ui/audio_resilient_app.py").read_text(encoding="utf-8")

    assert "AudioPreflightUseCase" in source
    assert "self.audio_preflight_use_case.run" in source
    assert "def _device_test_ready(self, result: AudioPreflightResult)" in source
    assert "super()._begin_preflight" not in source
    assert "json.loads" not in source
