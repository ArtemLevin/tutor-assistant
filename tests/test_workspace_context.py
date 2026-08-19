from datetime import date

from tutor_assistant.application.workspace import WorkspaceContextCoordinator
from tutor_assistant.domain import JobStatus, Lesson, Student


def _lesson(student_id: str, name: str, topic: str, status: JobStatus) -> Lesson:
    return Lesson(
        student=Student(id=student_id, full_name=name),
        subject="mathematics",
        lesson_date=date(2026, 8, 19),
        topic=topic,
        status=status,
    )


def test_recording_and_review_contexts_remain_independent() -> None:
    recording = _lesson("a", "Ученик A", "Новый урок", JobStatus.RECORDING)
    review = _lesson("b", "Ученик B", "Предыдущий урок", JobStatus.REVIEW_REQUIRED)
    coordinator = WorkspaceContextCoordinator()

    snapshot = coordinator.sync(
        recording_lesson=recording,
        review_lesson=review,
        recording_active=True,
        recording_stopping=False,
        elapsed_seconds=73,
    )

    assert snapshot.recording is not None
    assert snapshot.recording.lesson_id == recording.lesson_id
    assert snapshot.review is not None
    assert snapshot.review.lesson_id == review.lesson_id
    assert snapshot.focus_lesson == snapshot.review
    assert snapshot.stop_lesson_id == recording.lesson_id
    assert snapshot.review_open_allowed is True
    assert snapshot.audio_playback_allowed is False
    assert snapshot.restore_recording_form is False


def test_stopping_keeps_recording_identity_after_capture_turns_inactive() -> None:
    recording = _lesson("a", "Ученик A", "Новый урок", JobStatus.RECORDING)
    coordinator = WorkspaceContextCoordinator()

    snapshot = coordinator.sync(
        recording_lesson=recording,
        review_lesson=None,
        recording_active=False,
        recording_stopping=True,
        elapsed_seconds=120,
    )

    assert snapshot.recording_busy is True
    assert snapshot.stop_lesson_id == recording.lesson_id
    assert snapshot.audio_playback_allowed is False


def test_sync_refreshes_review_status_without_changing_recording_context() -> None:
    recording = _lesson("a", "Ученик A", "Новый урок", JobStatus.RECORDING)
    review = _lesson("b", "Ученик B", "Предыдущий урок", JobStatus.REVIEW_REQUIRED)
    coordinator = WorkspaceContextCoordinator()
    first = coordinator.sync(
        recording_lesson=recording,
        review_lesson=review,
        recording_active=True,
        recording_stopping=False,
        elapsed_seconds=1,
    )

    review.transition(JobStatus.READY)
    second = coordinator.sync(
        recording_lesson=recording,
        review_lesson=review,
        recording_active=True,
        recording_stopping=False,
        elapsed_seconds=1,
    )

    assert second.revision > first.revision
    assert second.recording == first.recording
    assert second.review is not None
    assert second.review.status == JobStatus.READY


def test_stale_review_can_be_invalidated_without_touching_recording() -> None:
    recording = _lesson("a", "Ученик A", "Новый урок", JobStatus.RECORDING)
    review = _lesson("b", "Ученик B", "Предыдущий урок", JobStatus.REVIEW_REQUIRED)
    coordinator = WorkspaceContextCoordinator()
    coordinator.sync(
        recording_lesson=recording,
        review_lesson=review,
        recording_active=True,
        recording_stopping=False,
        elapsed_seconds=0,
    )

    snapshot = coordinator.invalidate_review(review.lesson_id)

    assert snapshot.review is None
    assert snapshot.recording is not None
    assert snapshot.recording.lesson_id == recording.lesson_id
    assert snapshot.stop_lesson_id == recording.lesson_id
