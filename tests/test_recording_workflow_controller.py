from pathlib import Path

import pytest

from tutor_assistant.application.recording import (
    RecordingRuntimeState,
    RecordingWorkflowController,
    RecordingWorkflowPhase,
    RecordingWorkflowRejected,
)


def test_start_preparation_observes_active_runtime() -> None:
    controller = RecordingWorkflowController()

    controller.begin_start(RecordingRuntimeState())
    assert controller.phase == RecordingWorkflowPhase.PREPARING

    controller.observe_runtime(RecordingRuntimeState(active=True))
    assert controller.phase == RecordingWorkflowPhase.RECORDING
    assert controller.busy


def test_start_rejects_shutdown_and_duplicate_recording() -> None:
    controller = RecordingWorkflowController()

    with pytest.raises(RecordingWorkflowRejected, match="завершает фоновые операции"):
        controller.begin_start(RecordingRuntimeState(shutdown_requested=True))

    controller.begin_start(RecordingRuntimeState())
    controller.observe_runtime(RecordingRuntimeState(active=True))
    with pytest.raises(RecordingWorkflowRejected, match="уже запущена или сохраняется"):
        controller.begin_start(RecordingRuntimeState(active=True))


def test_aborted_preflight_returns_to_idle() -> None:
    controller = RecordingWorkflowController()
    controller.begin_start(RecordingRuntimeState())

    controller.abort_start()

    assert controller.phase == RecordingWorkflowPhase.IDLE
    assert not controller.busy


def test_stop_is_idempotent_and_tracks_runtime() -> None:
    controller = RecordingWorkflowController()
    controller.begin_start(RecordingRuntimeState())
    controller.observe_runtime(RecordingRuntimeState(active=True))

    assert controller.begin_stop(RecordingRuntimeState(active=True))
    assert controller.phase == RecordingWorkflowPhase.STOPPING
    assert not controller.begin_stop(RecordingRuntimeState(active=True, stopping=True))

    controller.mark_completed()
    assert controller.phase == RecordingWorkflowPhase.IDLE


def test_recovery_required_is_terminal_until_next_start() -> None:
    controller = RecordingWorkflowController()
    controller.mark_recovery_required()
    controller.observe_runtime(RecordingRuntimeState())
    assert controller.phase == RecordingWorkflowPhase.RECOVERY_REQUIRED

    controller.begin_start(RecordingRuntimeState())
    assert controller.phase == RecordingWorkflowPhase.PREPARING


def test_failed_finalization_is_visible_but_does_not_block_future_start() -> None:
    controller = RecordingWorkflowController()
    controller.mark_failed()
    assert controller.phase == RecordingWorkflowPhase.FAILED

    controller.begin_start(RecordingRuntimeState())
    assert controller.phase == RecordingWorkflowPhase.PREPARING


def test_production_entrypoint_routes_recording_commands_through_application_controller() -> None:
    source = Path("src/tutor_assistant/ui/audio_resilient_app.py").read_text(encoding="utf-8")

    assert "RecordingWorkflowController" in source
    assert "recording_workflow.begin_start" in source
    assert "recording_workflow.begin_stop" in source
    assert "recording_workflow.mark_recovery_required" in source
    assert "recording_workflow.mark_completed" in source
