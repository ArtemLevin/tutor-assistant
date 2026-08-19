from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..application.workspace import WorkspaceContextSnapshot


class ProcessingAction(StrEnum):
    OPEN = "open"
    RETRY = "retry"
    WAIT = "wait"


@dataclass(frozen=True)
class ParallelReviewPolicy:
    """Compatibility facade over the typed workspace policy."""

    recording_active: bool = False
    recording_stopping: bool = False

    @classmethod
    def from_workspace(cls, workspace: WorkspaceContextSnapshot) -> ParallelReviewPolicy:
        return cls(
            recording_active=workspace.recording_active,
            recording_stopping=workspace.recording_stopping,
        )

    @property
    def recording_busy(self) -> bool:
        return self.recording_active or self.recording_stopping

    @property
    def review_open_allowed(self) -> bool:
        return True

    @property
    def audio_playback_allowed(self) -> bool:
        return not self.recording_busy

    @property
    def restore_recording_form(self) -> bool:
        return not self.recording_busy


def processing_action(status: str) -> ProcessingAction:
    if status == "ready":
        return ProcessingAction.OPEN
    if status == "failed":
        return ProcessingAction.RETRY
    return ProcessingAction.WAIT


def format_elapsed(seconds: int) -> str:
    hours = max(0, seconds) // 3600
    minutes = (max(0, seconds) % 3600) // 60
    remaining = max(0, seconds) % 60
    return f"{hours:02d}:{minutes:02d}:{remaining:02d}"


def parallel_context_text(
    workspace: WorkspaceContextSnapshot | None = None,
    *,
    recording_student: str | None = None,
    recording_topic: str | None = None,
    review_student: str | None = None,
    review_topic: str | None = None,
    elapsed_seconds: int = 0,
) -> str:
    """Render recording + review from one snapshot.

    Keyword arguments remain for non-production compatibility while callers
    migrate; production synchronization passes ``WorkspaceContextSnapshot``.
    """

    if workspace is not None:
        recording = workspace.recording if workspace.recording_busy else None
        review = workspace.review
        recording_student = recording.student.full_name if recording else None
        recording_topic = recording.topic if recording else None
        review_student = review.student.full_name if review else None
        review_topic = review.topic if review else None
        elapsed_seconds = workspace.elapsed_seconds

    parts: list[str] = []
    if recording_student:
        recording = f"● {format_elapsed(elapsed_seconds)} · Запись: {recording_student}"
        if recording_topic:
            recording += f" — {recording_topic}"
        parts.append(recording)
    if review_student:
        review = f"Проверка: {review_student}"
        if review_topic:
            review += f" — {review_topic}"
        parts.append(review)
    return "\n".join(parts)
