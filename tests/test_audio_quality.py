from __future__ import annotations

import json

import numpy as np
import soundfile as sf

from tutor_assistant.recording.quality import analyze_track, create_quality_report


def test_quality_report_accepts_audible_tracks(tmp_path) -> None:
    rate = 8_000
    axis = np.arange(rate * 2) / rate
    signal = (0.2 * np.sin(2 * np.pi * 440 * axis)).astype("float32")[:, np.newaxis]
    microphone = tmp_path / "microphone.wav"
    system = tmp_path / "system.wav"
    output = tmp_path / "audio_quality_report.json"
    sf.write(microphone, signal, rate)
    sf.write(system, signal * 0.5, rate)

    report = create_quality_report(microphone, system, output)

    assert report.ready
    assert report.microphone.duration_seconds == 2.0
    assert report.microphone.rms > 0.1
    assert json.loads(output.read_text(encoding="utf-8"))["ready"] is True


def test_quality_report_rejects_silent_track(tmp_path) -> None:
    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros((16_000, 1), dtype="float32"), 8_000)

    quality = analyze_track(path)

    assert not quality.ready
    assert quality.silence_ratio == 1.0
    assert quality.warnings
