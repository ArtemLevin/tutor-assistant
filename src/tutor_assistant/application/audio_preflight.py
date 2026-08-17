from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Protocol


class AudioPreflightRecording(Protocol):
    microphone_file: Path
    system_file: Path
    quality_report: Path


class AudioPreflightRecorder(Protocol):
    @property
    def active(self) -> bool: ...

    def start(self, output_dir: Path, mic_device: int, system_source: object) -> None: ...

    def stop(self) -> AudioPreflightRecording: ...


@dataclass(frozen=True, slots=True)
class AudioPreflightResult:
    """UI-neutral result of a diagnostic microphone/system capture."""

    ready: bool
    microphone_rms: float
    system_rms: float
    warnings: tuple[str, ...]
    microphone_file: Path
    system_file: Path
    quality_report: Path


class AudioPreflightUseCase:
    """Own the diagnostic capture transaction and quality-report interpretation."""

    def __init__(
        self,
        recorder_factory: Callable[[int], AudioPreflightRecorder],
        *,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self._recorder_factory = recorder_factory
        self._sleeper = sleeper

    def run(
        self,
        directory: Path,
        mic_device: int,
        system_source: object,
        seconds: float,
        chunk_seconds: int,
    ) -> AudioPreflightResult:
        if seconds <= 0:
            raise ValueError("Длительность аудиодиагностики должна быть положительной")

        effective_chunk_seconds = max(chunk_seconds, int(seconds) + 1)
        recorder = self._recorder_factory(effective_chunk_seconds)
        stop_attempted = False
        try:
            recorder.start(directory, mic_device, system_source)
            self._sleeper(seconds)
            stop_attempted = True
            recording = recorder.stop()
        except Exception:
            if not stop_attempted and recorder.active:
                try:
                    recorder.stop()
                except Exception:
                    pass
            raise

        try:
            quality = json.loads(recording.quality_report.read_text(encoding="utf-8"))
            microphone = quality["microphone"]
            system = quality["system"]
            microphone_rms = float(microphone["rms"])
            system_rms = float(system["rms"])
            warnings = tuple(str(item) for item in quality.get("warnings", ()))
            ready = bool(quality.get("ready"))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise RuntimeError(
                f"Не удалось прочитать отчёт аудиодиагностики: {recording.quality_report}"
            ) from exc

        return AudioPreflightResult(
            ready=ready,
            microphone_rms=microphone_rms,
            system_rms=system_rms,
            warnings=warnings,
            microphone_file=recording.microphone_file,
            system_file=recording.system_file,
            quality_report=recording.quality_report,
        )
