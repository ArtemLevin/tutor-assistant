import json

import numpy as np
import pytest
import soundfile as sf

from tutor_assistant.recording.output import recover_recording as recover_output_recording
from tutor_assistant.recording.recorder import find_recoverable_recordings, recover_recording
from tutor_assistant.recording.session import (
    InvalidRecordingStatus,
    InvalidRecordingTransition,
    RecordingStatus,
    recording_status,
    transition_recording_status,
)


def _create_recording(tmp_path, *, status: str = "recording"):
    recording = tmp_path / "recording"
    mic = recording / "chunks" / "microphone"
    system = recording / "chunks" / "system"
    mic.mkdir(parents=True)
    system.mkdir(parents=True)
    sample_rate = 8_000
    payload = np.full((800, 1), 0.1, dtype="float32")
    for directory, prefix in ((mic, "mic"), (system, "system")):
        sf.write(directory / f"{prefix}_00000.wav", payload, sample_rate)
        sf.write(directory / f"{prefix}_00001.wav", payload, sample_rate)
    (recording / "session.json").write_text(
        json.dumps({"sample_rate": sample_rate, "channels": 1, "status": status}),
        encoding="utf-8",
    )
    return recording


def test_recover_recording_concatenates_chunks(tmp_path) -> None:
    recording = _create_recording(tmp_path)
    result = recover_recording(recording)
    info = sf.info(result.microphone_file)
    assert info.frames == 1600
    assert result.mixed_file.exists()
    assert result.quality_report.exists()


def test_wav_recovery_is_idempotent(tmp_path) -> None:
    recording = _create_recording(tmp_path)

    first = recover_recording(recording)
    first_bytes = {
        "microphone": first.microphone_file.read_bytes(),
        "system": first.system_file.read_bytes(),
        "mixed": first.mixed_file.read_bytes(),
    }
    first_sync = json.loads(first.sync_report.read_text(encoding="utf-8"))

    second = recover_recording(recording)

    assert second.microphone_file.read_bytes() == first_bytes["microphone"]
    assert second.system_file.read_bytes() == first_bytes["system"]
    assert second.mixed_file.read_bytes() == first_bytes["mixed"]
    assert json.loads(second.sync_report.read_text(encoding="utf-8")) == first_sync


def test_output_recovery_is_idempotent_and_canonicalizes_status(tmp_path) -> None:
    recording = _create_recording(tmp_path)

    first = recover_output_recording(recording, "wav")
    first_bytes = first.mixed_file.read_bytes()
    second = recover_output_recording(recording, "wav")

    assert second.mixed_file.read_bytes() == first_bytes
    session = json.loads((recording / "session.json").read_text(encoding="utf-8"))
    assert session["status"] == RecordingStatus.COMPLETED.value
    assert session["output_format"] == "wav"


def test_recording_state_machine_rejects_unknown_and_illegal_states() -> None:
    with pytest.raises(InvalidRecordingStatus):
        recording_status("mystery")

    session = {"status": RecordingStatus.RECORDED.value}
    with pytest.raises(InvalidRecordingTransition):
        transition_recording_status(session, RecordingStatus.FAILED_TO_START)

    transition_recording_status(session, RecordingStatus.COMPLETED)
    assert session["status"] == RecordingStatus.COMPLETED.value


def test_encoding_failed_session_is_discoverable_for_recovery(tmp_path) -> None:
    recording = tmp_path / "lessons" / "lesson-1" / "recording"
    chunks = recording / "chunks" / "microphone"
    chunks.mkdir(parents=True)
    (chunks / "mic_00000.wav").write_bytes(b"recoverable")
    (recording / "session.json").write_text(
        json.dumps({"status": "encoding_failed", "output_format": "m4a"}),
        encoding="utf-8",
    )

    assert find_recoverable_recordings(tmp_path) == [recording]
