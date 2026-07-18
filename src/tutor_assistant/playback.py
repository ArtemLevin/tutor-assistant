from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class PlaybackBackend(Protocol):
    def load(self, path: Path) -> None: ...

    def play(self) -> None: ...

    def pause(self) -> None: ...

    def stop(self) -> None: ...

    def set_position(self, position_ms: int) -> None: ...

    def position_ms(self) -> int: ...

    def set_rate(self, rate: float) -> None: ...

    def is_playing(self) -> bool: ...


class StopScheduler(Protocol):
    def schedule(self, delay_ms: int, callback: Callable[[], None]) -> None: ...

    def cancel(self) -> None: ...


@dataclass(frozen=True)
class PlaybackSegment:
    start_seconds: float
    end_seconds: float
    text: str = ""
    speaker: str | None = None

    @property
    def start_ms(self) -> int:
        return round(self.start_seconds * 1000)

    @property
    def end_ms(self) -> int:
        return round(self.end_seconds * 1000)


@dataclass(frozen=True)
class SegmentLoadResult:
    segments: tuple[PlaybackSegment, ...] = ()
    error: str | None = None


def load_playback_segments(path: Path) -> SegmentLoadResult:
    if not path.is_file():
        return SegmentLoadResult(error=f"Файл сегментов отсутствует: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return SegmentLoadResult(error=f"Не удалось прочитать сегменты: {exc}")
    if isinstance(payload, dict):
        payload = payload.get("segments")
    if not isinstance(payload, list):
        return SegmentLoadResult(error="Файл сегментов должен содержать JSON-массив")

    segments: list[PlaybackSegment] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            start = max(0.0, float(item["start"]))
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        speaker = str(item["speaker"]).strip() if item.get("speaker") else None
        segments.append(
            PlaybackSegment(
                start_seconds=start,
                end_seconds=end,
                text=str(item.get("text") or "").strip(),
                speaker=speaker,
            )
        )
    if payload and not segments:
        return SegmentLoadResult(error="В файле нет корректных временных сегментов")
    return SegmentLoadResult(tuple(segments))


def format_playback_time(position_ms: int) -> str:
    total_seconds = max(0, position_ms) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def audio_track_label(path: Path) -> str:
    stem = path.stem.casefold()
    if stem in {"lesson", "mixed", "mix"}:
        return "Смешанная дорожка"
    if stem in {"microphone", "mic", "teacher"}:
        return "Преподаватель"
    if stem in {"system", "student", "loopback"}:
        return "Ученик"
    return path.name


class PlaybackController:
    """Single audio session shared by archive, review and device diagnostics."""

    def __init__(
        self,
        backend: PlaybackBackend,
        scheduler: StopScheduler,
        playback_allowed: Callable[[], bool],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.backend = backend
        self.scheduler = scheduler
        self.playback_allowed = playback_allowed
        self.on_error = on_error
        self.current_path: Path | None = None
        self.current_rate = 1.0
        self.segment_end_ms: int | None = None
        self.last_error = ""

    def play_file(
        self,
        path: Path,
        *,
        rate: float = 1.0,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> bool:
        if not self.playback_allowed():
            return self._fail(
                "Во время записи нельзя воспроизводить старое аудио: оно попадёт в WASAPI Loopback."
            )
        resolved = path.resolve()
        if not resolved.is_file():
            return self._fail(f"Аудиофайл отсутствует: {path}")
        if not 0.5 <= rate <= 2.0:
            return self._fail("Скорость воспроизведения должна быть от 0,5× до 2×")

        position = max(0, start_ms if start_ms is not None else 0)
        if end_ms is not None and end_ms <= position:
            return self._fail("Конец сегмента должен быть позже его начала")
        try:
            self.scheduler.cancel()
            if self.current_path != resolved:
                self.backend.stop()
                self.backend.load(resolved)
                self.current_path = resolved
            self.current_rate = rate
            self.segment_end_ms = end_ms
            self.backend.set_rate(rate)
            if start_ms is not None or self.backend.position_ms() <= 0:
                self.backend.set_position(position)
            self.backend.play()
            self._schedule_segment_stop()
        except Exception as exc:
            return self._fail(f"Не удалось воспроизвести {path.name}: {exc}")
        self.last_error = ""
        return True

    def play_segment(self, path: Path, segment: PlaybackSegment, *, rate: float = 1.0) -> bool:
        return self.play_file(
            path,
            rate=rate,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
        )

    def toggle(self, path: Path, *, rate: float = 1.0) -> bool:
        resolved = path.resolve()
        if self.current_path == resolved and self.backend.is_playing():
            self.pause()
            return True
        start_ms = None if self.current_path == resolved else 0
        return self.play_file(
            resolved,
            rate=rate,
            start_ms=start_ms,
            end_ms=self.segment_end_ms if self.current_path == resolved else None,
        )

    def pause(self) -> None:
        self.scheduler.cancel()
        try:
            self.backend.pause()
        except Exception as exc:
            self._fail(f"Не удалось приостановить воспроизведение: {exc}")

    def stop(self, *, clear_source: bool = False) -> None:
        self.scheduler.cancel()
        try:
            self.backend.stop()
        except Exception as exc:
            self._notify_error(f"Не удалось остановить воспроизведение: {exc}")
        self.segment_end_ms = None
        if clear_source:
            self.current_path = None

    def seek(self, path: Path, position_ms: int) -> bool:
        resolved = path.resolve()
        if not resolved.is_file():
            return self._fail(f"Аудиофайл отсутствует: {path}")
        try:
            if self.current_path != resolved:
                self.backend.stop()
                self.backend.load(resolved)
                self.current_path = resolved
            self.segment_end_ms = None
            self.scheduler.cancel()
            self.backend.set_position(max(0, position_ms))
        except Exception as exc:
            return self._fail(f"Не удалось перейти к позиции: {exc}")
        self.last_error = ""
        return True

    def set_rate(self, rate: float) -> bool:
        if not 0.5 <= rate <= 2.0:
            return self._fail("Скорость воспроизведения должна быть от 0,5× до 2×")
        try:
            self.current_rate = rate
            self.backend.set_rate(rate)
            if self.backend.is_playing():
                self._schedule_segment_stop()
        except Exception as exc:
            return self._fail(f"Не удалось изменить скорость: {exc}")
        return True

    def prepare_recording(self) -> None:
        """Stop every application-owned playback before opening loopback capture."""

        self.stop(clear_source=True)

    def report_backend_error(self, message: str) -> None:
        self._fail(f"Не удалось прочитать аудиофайл: {message or 'неизвестная ошибка'}")

    def _schedule_segment_stop(self) -> None:
        self.scheduler.cancel()
        if self.segment_end_ms is None:
            return
        remaining = self.segment_end_ms - self.backend.position_ms()
        if remaining <= 0:
            self.pause()
            return
        self.scheduler.schedule(
            max(100, round(remaining / self.current_rate)),
            self._finish_segment,
        )

    def _finish_segment(self) -> None:
        self.segment_end_ms = None
        self.pause()

    def _fail(self, message: str) -> bool:
        self.scheduler.cancel()
        try:
            self.backend.stop()
        except Exception:
            pass
        self.last_error = message
        self._notify_error(message)
        return False

    def _notify_error(self, message: str) -> None:
        if self.on_error:
            self.on_error(message)
