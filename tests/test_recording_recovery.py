import json

import numpy as np
import soundfile as sf

from tutor_assistant.recording.recorder import recover_recording


def test_recover_recording_concatenates_chunks(tmp_path) -> None:
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
        json.dumps({"sample_rate": sample_rate, "channels": 1, "status": "recording"}), encoding="utf-8"
    )
    result = recover_recording(recording)
    info = sf.info(result.microphone_file)
    assert info.frames == 1600
    assert result.mixed_file.exists()
