from __future__ import annotations

from tutor_assistant.application.shutdown import (
    ShutdownCloseAction,
    ShutdownCoordinator,
    ShutdownDrainAction,
    ShutdownPhase,
    ShutdownRuntimeSnapshot,
)


def test_idle_runtime_uses_immediate_transcription_shutdown_without_prompt() -> None:
    coordinator = ShutdownCoordinator(immediate_wait_ms=750)

    decision = coordinator.request_close(ShutdownRuntimeSnapshot())

    assert decision.action == ShutdownCloseAction.TRY_IMMEDIATE
    assert decision.transcription_wait_ms == 750
    assert coordinator.phase == ShutdownPhase.IDLE


def test_immediate_shutdown_success_marks_ready_for_accept() -> None:
    coordinator = ShutdownCoordinator()
    coordinator.request_close(ShutdownRuntimeSnapshot())

    assert coordinator.complete_immediate_shutdown(transcription_stopped=True) == ShutdownPhase.READY
    assert coordinator.request_close(ShutdownRuntimeSnapshot()).action == ShutdownCloseAction.ACCEPT


def test_immediate_wait_failure_enters_draining_and_suppresses_repeat_close() -> None:
    coordinator = ShutdownCoordinator()
    coordinator.request_close(ShutdownRuntimeSnapshot())

    assert coordinator.complete_immediate_shutdown(transcription_stopped=False) == ShutdownPhase.DRAINING
    assert coordinator.request_close(ShutdownRuntimeSnapshot()).action == ShutdownCloseAction.IGNORE


def test_busy_recording_requires_confirmation() -> None:
    coordinator = ShutdownCoordinator()
    snapshot = ShutdownRuntimeSnapshot(recording_active=True)

    assert coordinator.request_close(snapshot).action == ShutdownCloseAction.PROMPT
    assert coordinator.phase == ShutdownPhase.IDLE


def test_background_worker_or_busy_transcription_requires_confirmation() -> None:
    coordinator = ShutdownCoordinator()

    assert coordinator.request_close(
        ShutdownRuntimeSnapshot(workers_running=True)
    ).action == ShutdownCloseAction.PROMPT
    assert coordinator.request_close(
        ShutdownRuntimeSnapshot(transcription_busy=True)
    ).action == ShutdownCloseAction.PROMPT


def test_user_cancel_keeps_shutdown_idle_and_has_no_side_effect_plan() -> None:
    coordinator = ShutdownCoordinator()
    snapshot = ShutdownRuntimeSnapshot(recording_active=True, normalization_cancellable=True)

    plan = coordinator.confirm_close(snapshot, confirmed=False)

    assert not plan.begin_draining
    assert not plan.cancel_normalization
    assert not plan.shutdown_transcription
    assert not plan.quiesce_runtime
    assert not plan.finalize_recording
    assert coordinator.phase == ShutdownPhase.IDLE


def test_confirmed_shutdown_emits_one_complete_drain_plan() -> None:
    coordinator = ShutdownCoordinator()
    snapshot = ShutdownRuntimeSnapshot(
        recording_active=True,
        workers_running=True,
        transcription_busy=True,
        transcription_running=True,
        normalization_cancellable=True,
    )

    plan = coordinator.confirm_close(snapshot, confirmed=True)

    assert plan.begin_draining
    assert plan.cancel_normalization
    assert plan.shutdown_transcription
    assert plan.quiesce_runtime
    assert plan.finalize_recording
    assert coordinator.phase == ShutdownPhase.DRAINING


def test_recording_stop_in_flight_is_barrier_but_not_restarted() -> None:
    coordinator = ShutdownCoordinator()
    snapshot = ShutdownRuntimeSnapshot(
        recording_stop_in_flight=True,
        normalization_cancellable=True,
    )

    plan = coordinator.confirm_close(snapshot, confirmed=True)

    assert plan.begin_draining
    assert not plan.finalize_recording
    assert coordinator.observe_drain(snapshot) == ShutdownDrainAction.WAIT


def test_drain_waits_for_recording_workers_and_transcription_thread_independently() -> None:
    coordinator = ShutdownCoordinator()
    coordinator.confirm_close(
        ShutdownRuntimeSnapshot(workers_running=True),
        confirmed=True,
    )

    assert coordinator.observe_drain(
        ShutdownRuntimeSnapshot(recording_active=True)
    ) == ShutdownDrainAction.WAIT
    assert coordinator.observe_drain(
        ShutdownRuntimeSnapshot(workers_running=True)
    ) == ShutdownDrainAction.WAIT
    assert coordinator.observe_drain(
        ShutdownRuntimeSnapshot(transcription_running=True)
    ) == ShutdownDrainAction.WAIT


def test_drained_runtime_transitions_ready_and_schedules_close_once() -> None:
    coordinator = ShutdownCoordinator()
    coordinator.confirm_close(
        ShutdownRuntimeSnapshot(workers_running=True),
        confirmed=True,
    )

    assert coordinator.observe_drain(ShutdownRuntimeSnapshot()) == ShutdownDrainAction.SCHEDULE_CLOSE
    assert coordinator.phase == ShutdownPhase.READY
    assert coordinator.observe_drain(ShutdownRuntimeSnapshot()) == ShutdownDrainAction.NONE
    assert coordinator.request_close(ShutdownRuntimeSnapshot()).action == ShutdownCloseAction.ACCEPT


def test_repeat_confirmation_during_draining_does_not_duplicate_cancel_or_finalize() -> None:
    coordinator = ShutdownCoordinator()
    snapshot = ShutdownRuntimeSnapshot(
        recording_active=True,
        normalization_cancellable=True,
    )
    first = coordinator.confirm_close(snapshot, confirmed=True)

    repeated = coordinator.confirm_close(snapshot, confirmed=True)

    assert first.cancel_normalization
    assert first.finalize_recording
    assert not repeated.begin_draining
    assert not repeated.cancel_normalization
    assert not repeated.finalize_recording


def test_idle_transcription_thread_running_is_not_itself_a_prompt_reason() -> None:
    coordinator = ShutdownCoordinator()

    decision = coordinator.request_close(
        ShutdownRuntimeSnapshot(transcription_running=True, transcription_busy=False)
    )

    assert decision.action == ShutdownCloseAction.TRY_IMMEDIATE
