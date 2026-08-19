from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..domain import JobStatus, Lesson


@dataclass(frozen=True, slots=True)
class WorkspaceStudentContext:
    """Immutable student identity exposed to workspace presentation."""

    id: str
    full_name: str


@dataclass(frozen=True, slots=True)
class LessonWorkspaceContext:
    """Immutable lesson facts required to render a workspace context."""

    lesson_id: str
    student: WorkspaceStudentContext
    subject: str
    lesson_date: date
    topic: str
    status: JobStatus
    source_audio_local: str | None = None

    @classmethod
    def from_lesson(cls, lesson: Lesson) -> LessonWorkspaceContext:
        return cls(
            lesson_id=lesson.lesson_id,
            student=WorkspaceStudentContext(
                id=lesson.student.id,
                full_name=lesson.student.full_name,
            ),
            subject=lesson.subject,
            lesson_date=lesson.lesson_date,
            topic=lesson.topic,
            status=lesson.status,
            source_audio_local=lesson.source_audio_local,
        )


@dataclass(frozen=True, slots=True)
class WorkspaceContextSnapshot:
    """Typed state shared by recording, review and cockpit presentation."""

    recording: LessonWorkspaceContext | None = None
    review: LessonWorkspaceContext | None = None
    recording_active: bool = False
    recording_stopping: bool = False
    elapsed_seconds: int = 0
    revision: int = 0

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

    @property
    def focus_lesson(self) -> LessonWorkspaceContext | None:
        """Lesson rendered by review-oriented controls and Teacher Cockpit."""

        return self.review or self.recording

    @property
    def stop_lesson_id(self) -> str | None:
        """Recording identity targeted by the global stop action."""

        if not self.recording_busy or self.recording is None:
            return None
        return self.recording.lesson_id


class WorkspaceContextCoordinator:
    """Own independent recording/review contexts outside Qt.

    The coordinator deliberately stores immutable projections instead of mutable
    ``Lesson`` objects. UI adapters re-sync after domain transitions, while the
    30-second cockpit timer remains a defensive fallback for external changes.
    """

    def __init__(self) -> None:
        self._recording: LessonWorkspaceContext | None = None
        self._review: LessonWorkspaceContext | None = None
        self._recording_active = False
        self._recording_stopping = False
        self._elapsed_seconds = 0
        self._revision = 0

    @property
    def snapshot(self) -> WorkspaceContextSnapshot:
        return WorkspaceContextSnapshot(
            recording=self._recording,
            review=self._review,
            recording_active=self._recording_active,
            recording_stopping=self._recording_stopping,
            elapsed_seconds=self._elapsed_seconds,
            revision=self._revision,
        )

    def sync(
        self,
        *,
        recording_lesson: Lesson | None,
        review_lesson: Lesson | None,
        recording_active: bool,
        recording_stopping: bool,
        elapsed_seconds: int,
    ) -> WorkspaceContextSnapshot:
        next_recording = (
            LessonWorkspaceContext.from_lesson(recording_lesson)
            if recording_lesson is not None
            else None
        )
        next_review = (
            LessonWorkspaceContext.from_lesson(review_lesson)
            if review_lesson is not None
            else None
        )
        next_active = bool(recording_active)
        next_stopping = bool(recording_stopping)
        next_elapsed = max(0, int(elapsed_seconds))

        next_state = (
            next_recording,
            next_review,
            next_active,
            next_stopping,
            next_elapsed,
        )
        current_state = (
            self._recording,
            self._review,
            self._recording_active,
            self._recording_stopping,
            self._elapsed_seconds,
        )
        if next_state != current_state:
            self._recording = next_recording
            self._review = next_review
            self._recording_active = next_active
            self._recording_stopping = next_stopping
            self._elapsed_seconds = next_elapsed
            self._revision += 1
        return self.snapshot

    def invalidate_review(self, lesson_id: str) -> WorkspaceContextSnapshot:
        if self._review is not None and self._review.lesson_id == lesson_id:
            self._review = None
            self._revision += 1
        return self.snapshot

    def reset(self) -> WorkspaceContextSnapshot:
        if any(
            (
                self._recording is not None,
                self._review is not None,
                self._recording_active,
                self._recording_stopping,
                self._elapsed_seconds,
            )
        ):
            self._recording = None
            self._review = None
            self._recording_active = False
            self._recording_stopping = False
            self._elapsed_seconds = 0
            self._revision += 1
        return self.snapshot
