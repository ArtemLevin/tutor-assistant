from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProcessingAction(StrEnum):
    OPEN = "open"
    RETRY = "retry"
    WAIT = "wait"


@dataclass(frozen=True)
class ParallelReviewPolicy:
    """Rules for reviewing a completed lesson while another lesson is recorded."""

    recording_active: bool = False
    recording_stopping: bool = False

    @property
    def recording_busy(self) -> bool:
        return self.recording_active or self.recording_stopping

    @property
    def review_open_allowed(self) -> bool:
        # Opening text files does not touch the recorder or its captured lesson context.
        return True

    @property
    def audio_playback_allowed(self) -> bool:
        # Playback would be captured by WASAPI Loopback and contaminate the active lesson.
        return not self.recording_busy

    @property
    def restore_recording_form(self) -> bool:
        # Do not replace student/topic/date controls that describe the active/new lesson.
        return not self.recording_busy


def processing_action(status: str) -> ProcessingAction:
    if status == "ready":
        return ProcessingAction.OPEN
    if status == "failed":
        return ProcessingAction.RETRY
    return ProcessingAction.WAIT


def parallel_context_text(
    *,
    recording_student: str | None = None,
    recording_topic: str | None = None,
    review_student: str | None = None,
    review_topic: str | None = None,
    elapsed_seconds: int = 0,
) -> str:
    lines: list[str] = []
    if recording_student:
        hours, remainder = divmod(max(0, elapsed_seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        topic = f" — {recording_topic}" if recording_topic else ""
        lines.append(f"● {hours:02d}:{minutes:02d}:{seconds:02d} · Запись: {recording_student}{topic}")
    if review_student:
        topic = f" — {review_topic}" if review_topic else ""
        lines.append(f"Проверка: {review_student}{topic}")
    return "\n".join(lines)
