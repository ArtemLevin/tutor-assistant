from tutor_assistant.ui.parallel_review import (
    ParallelReviewPolicy,
    ProcessingAction,
    parallel_context_text,
    processing_action,
)


def test_ready_transcript_can_open_during_recording() -> None:
    policy = ParallelReviewPolicy(recording_active=True)

    assert policy.review_open_allowed is True
    assert processing_action("ready") == ProcessingAction.OPEN
    assert policy.restore_recording_form is False


def test_audio_playback_is_blocked_until_recording_finishes() -> None:
    assert ParallelReviewPolicy(recording_active=True).audio_playback_allowed is False
    assert ParallelReviewPolicy(recording_stopping=True).audio_playback_allowed is False
    assert ParallelReviewPolicy().audio_playback_allowed is True


def test_failed_and_running_jobs_keep_their_existing_actions() -> None:
    assert processing_action("failed") == ProcessingAction.RETRY
    assert processing_action("running") == ProcessingAction.WAIT
    assert processing_action("waiting") == ProcessingAction.WAIT


def test_parallel_context_names_both_lessons() -> None:
    text = parallel_context_text(
        recording_student="Ученик B",
        recording_topic="Новая тема",
        review_student="Ученик A",
        review_topic="Предыдущая тема",
        elapsed_seconds=65,
    )

    assert "00:01:05" in text
    assert "Запись: Ученик B — Новая тема" in text
    assert "Проверка: Ученик A — Предыдущая тема" in text
