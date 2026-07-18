from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from tutor_assistant.playback import (
    PlaybackController,
    PlaybackSegment,
    audio_track_label,
    format_playback_time,
    load_playback_segments,
)


class FakeBackend:
    def __init__(self, *, load_error: Exception | None = None) -> None:
        self.load_error = load_error
        self.path: Path | None = None
        self.position = 0
        self.rate = 1.0
        self.playing = False
        self.play_calls = 0
        self.stop_calls = 0

    def load(self, path: Path) -> None:
        if self.load_error:
            raise self.load_error
        self.path = path

    def play(self) -> None:
        self.playing = True
        self.play_calls += 1

    def pause(self) -> None:
        self.playing = False

    def stop(self) -> None:
        self.playing = False
        self.position = 0
        self.stop_calls += 1

    def set_position(self, position_ms: int) -> None:
        self.position = position_ms

    def position_ms(self) -> int:
        return self.position

    def set_rate(self, rate: float) -> None:
        self.rate = rate

    def is_playing(self) -> bool:
        return self.playing


class FakeScheduler:
    def __init__(self) -> None:
        self.delay_ms: int | None = None
        self.callback: Callable[[], None] | None = None

    def schedule(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self.delay_ms = delay_ms
        self.callback = callback

    def cancel(self) -> None:
        self.delay_ms = None
        self.callback = None


def make_controller(
    backend: FakeBackend,
    scheduler: FakeScheduler,
    *,
    allowed: bool = True,
    errors: list[str] | None = None,
) -> PlaybackController:
    return PlaybackController(
        backend,
        scheduler,
        lambda: allowed,
        (errors if errors is not None else []).append,
    )


def test_segment_playback_seek_speed_and_pause(tmp_path: Path) -> None:
    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"RIFF-test")
    backend = FakeBackend()
    scheduler = FakeScheduler()
    controller = make_controller(backend, scheduler)
    segment = PlaybackSegment(1.25, 3.25, "Фрагмент", "У")

    assert controller.play_segment(audio, segment, rate=2.0)
    assert backend.path == audio.resolve()
    assert backend.position == 1250
    assert backend.rate == 2.0
    assert backend.playing
    assert scheduler.delay_ms == 1000
    assert scheduler.callback is not None
    scheduler.callback()
    assert not backend.playing
    assert controller.segment_end_ms is None

    assert controller.seek(audio, 2200)
    assert backend.position == 2200
    assert scheduler.delay_ms is None
    assert controller.play_file(audio, rate=1.25, start_ms=2200)
    controller.pause()
    assert not backend.playing


def test_recording_policy_blocks_old_audio_and_prepare_recording_stops_it(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "system.wav"
    audio.write_bytes(b"RIFF-test")
    errors: list[str] = []
    blocked_backend = FakeBackend()
    blocked = make_controller(
        blocked_backend,
        FakeScheduler(),
        allowed=False,
        errors=errors,
    )

    assert not blocked.play_file(audio)
    assert blocked_backend.play_calls == 0
    assert "WASAPI Loopback" in errors[-1]

    backend = FakeBackend()
    controller = make_controller(backend, FakeScheduler())
    assert controller.play_file(audio)
    controller.prepare_recording()
    assert not backend.playing
    assert controller.current_path is None


def test_missing_and_damaged_audio_are_reported_without_escaping_exception(
    tmp_path: Path,
) -> None:
    errors: list[str] = []
    controller = make_controller(FakeBackend(), FakeScheduler(), errors=errors)
    assert not controller.play_file(tmp_path / "missing.wav")
    assert "отсутствует" in errors[-1]

    damaged = tmp_path / "damaged.wav"
    damaged.write_bytes(b"not-a-wave")
    controller = make_controller(
        FakeBackend(load_error=ValueError("invalid media")),
        FakeScheduler(),
        errors=errors,
    )
    assert not controller.play_file(damaged)
    assert "invalid media" in errors[-1]


def test_segments_loader_skips_invalid_rows_and_handles_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "segments.json"
    path.write_text(
        json.dumps(
            [
                {"start": 0.5, "end": 1.75, "text": "Ответ", "speaker": "У"},
                {"start": 3, "end": 2, "text": "invalid"},
                {"text": "missing time"},
            ]
        ),
        encoding="utf-8",
    )

    result = load_playback_segments(path)
    assert result.error is None
    assert len(result.segments) == 1
    assert result.segments[0].start_ms == 500
    assert result.segments[0].speaker == "У"

    path.write_text("{broken", encoding="utf-8")
    result = load_playback_segments(path)
    assert result.segments == ()
    assert "Не удалось прочитать" in str(result.error)


def test_audio_labels_and_time_formatting() -> None:
    assert audio_track_label(Path("lesson.wav")) == "Смешанная дорожка"
    assert audio_track_label(Path("microphone.wav")) == "Преподаватель"
    assert audio_track_label(Path("system.wav")) == "Ученик"
    assert format_playback_time(65_000) == "01:05"
    assert format_playback_time(3_665_000) == "01:01:05"
