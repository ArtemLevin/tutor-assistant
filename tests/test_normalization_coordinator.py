from __future__ import annotations

from types import SimpleNamespace

from tutor_assistant.application.normalization import (
    NormalizationAfterWorkerAction,
    NormalizationAutoAction,
    NormalizationAutoContext,
    NormalizationCoordinator,
    NormalizationLifecycleState,
    NormalizationManualStartContext,
    NormalizationStartBlock,
)


def manual_context(**overrides):
    values = {
        "lesson_id": "lesson-1",
        "provider": "ollama",
        "provider_error": None,
        "has_segments": True,
        "transcription_busy": False,
    }
    values.update(overrides)
    return NormalizationManualStartContext(**values)


def test_manual_start_is_allowed_when_all_barriers_are_clear() -> None:
    coordinator = NormalizationCoordinator()

    decision = coordinator.evaluate_manual_start(manual_context())

    assert decision.allowed is True
    assert decision.block is None


def test_manual_start_preserves_current_barrier_order() -> None:
    coordinator = NormalizationCoordinator()

    assert coordinator.evaluate_manual_start(
        manual_context(lesson_id=None, provider_error="bad", transcription_busy=True)
    ).block == NormalizationStartBlock.NO_LESSON
    assert coordinator.evaluate_manual_start(
        manual_context(provider_error="bad", transcription_busy=True)
    ).block == NormalizationStartBlock.PROVIDER_ERROR
    assert coordinator.evaluate_manual_start(
        manual_context(transcription_busy=True)
    ).block == NormalizationStartBlock.TRANSCRIPTION_BUSY

    coordinator.begin("active")
    assert coordinator.evaluate_manual_start(
        manual_context(has_segments=False)
    ).block == NormalizationStartBlock.ALREADY_RUNNING


def test_no_segments_blocks_manual_start_after_runtime_barriers() -> None:
    coordinator = NormalizationCoordinator()

    decision = coordinator.evaluate_manual_start(manual_context(has_segments=False))

    assert decision.allowed is False
    assert decision.block == NormalizationStartBlock.NO_SEGMENTS


def test_non_ollama_provider_does_not_use_whisper_cpu_barrier() -> None:
    coordinator = NormalizationCoordinator()

    decision = coordinator.evaluate_manual_start(
        manual_context(provider="yandex_ai_studio", transcription_busy=True)
    )

    assert decision.allowed is True


def test_auto_queue_is_deduplicated_and_fifo() -> None:
    coordinator = NormalizationCoordinator()
    assert coordinator.enqueue_auto("one") is True
    assert coordinator.enqueue_auto("one") is False
    assert coordinator.enqueue_auto("two") is True

    first = coordinator.pump_auto(
        NormalizationAutoContext(
            provider="ollama",
            shutdown_requested=False,
            transcription_busy=False,
        )
    )

    assert first.action == NormalizationAutoAction.START
    assert first.lesson_id == "one"
    assert coordinator.active_lesson_id == "one"
    assert coordinator.pending_auto_count == 1

    coordinator.finish_worker()
    second = coordinator.pump_auto(
        NormalizationAutoContext(
            provider="ollama",
            shutdown_requested=False,
            transcription_busy=False,
        )
    )
    assert second.action == NormalizationAutoAction.START
    assert second.lesson_id == "two"


def test_auto_queue_respects_shutdown_and_whisper_barriers() -> None:
    coordinator = NormalizationCoordinator()
    coordinator.enqueue_auto("lesson")

    shutdown = coordinator.pump_auto(
        NormalizationAutoContext(
            provider="ollama",
            shutdown_requested=True,
            transcription_busy=False,
        )
    )
    busy = coordinator.pump_auto(
        NormalizationAutoContext(
            provider="ollama",
            shutdown_requested=False,
            transcription_busy=True,
        )
    )

    assert shutdown.action == NormalizationAutoAction.IDLE
    assert busy.action == NormalizationAutoAction.IDLE
    assert coordinator.pending_auto_count == 1
    assert coordinator.active is False


def test_yandex_auto_queue_waits_for_explicit_cloud_consent() -> None:
    coordinator = NormalizationCoordinator()
    coordinator.enqueue_auto("lesson")

    decision = coordinator.pump_auto(
        NormalizationAutoContext(
            provider="yandex_ai_studio",
            shutdown_requested=False,
            transcription_busy=False,
        )
    )

    assert decision.action == NormalizationAutoAction.WAITING_CLOUD_CONSENT
    assert decision.lesson_id is None
    assert coordinator.pending_auto_count == 1
    assert coordinator.active is False


def test_cancel_and_progress_are_lifecycle_state_not_widget_state() -> None:
    coordinator = NormalizationCoordinator()
    coordinator.begin("lesson")
    progress = coordinator.update_progress(
        SimpleNamespace(
            current_chunk=1,
            total_chunks=4,
            completed_chunks=1,
            reused_chunks=2,
            provider_requests=3,
            current_attempt=2,
            state="running",
        )
    )

    assert coordinator.state == NormalizationLifecycleState.RUNNING
    assert progress.current_chunk == 1
    assert coordinator.request_cancel() is True
    assert coordinator.state == NormalizationLifecycleState.CANCELLING


def test_resume_confirmation_drives_single_retry_after_worker() -> None:
    coordinator = NormalizationCoordinator()
    coordinator.begin("lesson")
    coordinator.record_resume_confirmation(True)

    first = coordinator.finish_worker()

    assert first == NormalizationAfterWorkerAction.RETRY_INDETERMINATE
    assert coordinator.state == NormalizationLifecycleState.IDLE
    assert coordinator.active_lesson_id is None
    assert coordinator.finish_worker() == NormalizationAfterWorkerAction.PUMP_AUTO


def test_declined_resume_returns_to_auto_pump() -> None:
    coordinator = NormalizationCoordinator()
    coordinator.begin("lesson")
    coordinator.record_resume_confirmation(False)

    assert coordinator.finish_worker() == NormalizationAfterWorkerAction.PUMP_AUTO
