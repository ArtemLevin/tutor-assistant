from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import numpy as np

from tutor_assistant.recording.devices import (
    AudioDevice,
    list_loopback_devices,
    list_system_audio_sources,
)
from tutor_assistant.recording.recorder import SoundCardLoopbackStream


class FakeDevice:
    def __init__(self, device_id: str, name: str, channels: int = 2) -> None:
        self.id = device_id
        self.name = name
        self.channels = channels


def test_loopback_devices_are_separated_from_microphones(monkeypatch) -> None:
    microphone = FakeDevice("mic-1", "Microphone G733", 1)
    g733 = FakeDevice("speaker-g733", "Speakers G733", 2)
    realtek = FakeDevice("speaker-realtek", "Speakers Realtek", 2)
    module = SimpleNamespace(
        all_microphones=lambda include_loopback=False: (
            [g733, realtek, microphone] if include_loopback else [microphone]
        ),
        default_speaker=lambda: g733,
    )
    monkeypatch.setitem(sys.modules, "soundcard", module)

    sources = list_loopback_devices()

    assert [source.name for source in sources] == ["Speakers G733", "Speakers Realtek"]
    assert sources[0].is_default
    assert all(source.backend == "soundcard" for source in sources)


def test_stereo_mix_remains_available_as_fallback(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "soundcard",
        SimpleNamespace(
            all_microphones=lambda include_loopback=False: [],
            default_speaker=lambda: None,
        ),
    )
    inputs = [AudioDevice(31, "Stereo Mix Realtek", 2, 48_000, "Windows WDM-KS")]

    sources = list_system_audio_sources(inputs)

    assert len(sources) == 1
    assert sources[0].backend == "sounddevice"
    assert sources[0].legacy_index == 31


def test_loopback_multichannel_audio_is_downmixed_after_capture() -> None:
    stereo = np.array([[1.0, -1.0], [0.5, 0.5]], dtype="float32")

    mono = SoundCardLoopbackStream.normalize_channels(stereo, 1)

    assert mono.shape == (2, 1)
    assert np.allclose(mono[:, 0], [0.0, 0.5])


def test_loopback_stream_delivers_blocks(monkeypatch) -> None:
    blocks: list[np.ndarray] = []

    class Recorder:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def record(self, numframes):
            time.sleep(0.002)
            return np.full((numframes, 2), 0.25, dtype="float32")

    device = SimpleNamespace(recorder=lambda **kwargs: Recorder())
    monkeypatch.setitem(
        sys.modules,
        "soundcard",
        SimpleNamespace(get_microphone=lambda device_id, include_loopback: device),
    )
    stream = SoundCardLoopbackStream("speaker-g733", 8_000, 1, blocks.append, block_frames=80)

    stream.start()
    deadline = time.monotonic() + 1
    while not blocks and time.monotonic() < deadline:
        time.sleep(0.01)
    stream.stop()

    assert blocks
    assert blocks[0].shape == (80, 1)
    assert np.allclose(blocks[0], 0.25)


def test_loopback_stream_reconnects_before_giving_up(monkeypatch) -> None:
    attempts = 0

    class Recorder:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def record(self, numframes):
            time.sleep(0.002)
            return np.ones((numframes, 2), dtype="float32")

    def get_microphone(device_id, include_loopback):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("device temporarily unavailable")
        return SimpleNamespace(recorder=lambda **kwargs: Recorder())

    monkeypatch.setitem(sys.modules, "soundcard", SimpleNamespace(get_microphone=get_microphone))
    blocks: list[np.ndarray] = []
    stream = SoundCardLoopbackStream("speaker-g733", 8_000, 1, blocks.append, block_frames=80)

    stream.start()
    stream.stop()

    assert attempts == 3
    assert stream.reconnect_attempts == 2
    assert stream.error is None
