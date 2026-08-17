from __future__ import annotations

from pathlib import Path

from tutor_assistant.application.recording_recovery import (
    RecordingRecoveryState,
    RecoverRecordingUseCase,
)
from tutor_assistant.recording import RecordingResult


def make_result(tmp_path: Path) -> RecordingResult:
    mixed = tmp_path / "lesson.wav"
    mixed.parent.mkdir(parents=True, exist_ok=True)
    mixed.write_bytes(b"audio")
    return RecordingResult(
        microphone_file=tmp_path / "microphone.wav",
        system_file=tmp_path / "system.wav",
        mixed_file=mixed,
        session_file=tmp_path / "session.json",
        sync_report=tmp_path / "sync.json",
        quality_report=tmp_path / "quality.json",
    )


def test_metadata_lookup_failure_does_not_prevent_durable_audio_recovery(tmp_path: Path) -> None:
    recording_dir = tmp_path / "lesson-1" / "recording"
    result = make_result(recording_dir)
    saver_called = False
    finalizer_called = False

    def lookup(_lesson_id: str):
        raise RuntimeError("database read boom")

    def saver(_lesson, _fields):
        nonlocal saver_called
        saver_called = True

    def finalizer(recovered, _lesson):
        nonlocal finalizer_called
        finalizer_called = True
        return recovered

    outcome = RecoverRecordingUseCase(
        discoverer=lambda _workspace: (),
        recoverer=lambda _directory: result,
        lesson_lookup=lookup,
        lesson_saver=saver,
        result_finalizer=finalizer,
    ).recover(recording_dir)

    assert outcome.state == RecordingRecoveryState.FAILED
    assert outcome.result is result
    assert outcome.lesson is None
    assert "database read boom" in (outcome.error or "")
    assert not saver_called
    assert not finalizer_called
