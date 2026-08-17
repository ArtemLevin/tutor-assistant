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


def test_recording_application_layer_remains_qt_free() -> None:
    source = Path("src/tutor_assistant/application/recording.py").read_text(encoding="utf-8")

    assert "PySide" not in source
    assert "QThread" not in source
    assert "QMessageBox" not in source


def test_production_recording_adapters_route_lifecycle_through_controller() -> None:
    start_source = Path("src/tutor_assistant/ui/audio_resilient_app.py").read_text(
        encoding="utf-8"
    )
    stop_source = Path("src/tutor_assistant/ui/recording_finalize_app.py").read_text(
        encoding="utf-8"
    )
    recovery_source = Path("src/tutor_assistant/ui/recording_recovery_app.py").read_text(
        encoding="utf-8"
    )

    assert "RecordingWorkflowController" in start_source
    assert "recording_workflow.begin_start" in start_source

    assert "recording_workflow.begin_stop" in stop_source
    assert "recording_workflow.mark_recovery_required" in stop_source
    assert "recording_workflow.mark_completed" in stop_source

    assert "recording_workflow.reset()" in recovery_source
    assert "recording_workflow.mark_recovery_required()" in recovery_source
