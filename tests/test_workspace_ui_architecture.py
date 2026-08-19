from pathlib import Path

ROOT = Path(__file__).parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_workspace_application_boundary_is_qt_free() -> None:
    source = _source("src/tutor_assistant/application/workspace.py")

    assert "PySide6" not in source
    assert "tutor_assistant.ui" not in source
    assert "class WorkspaceContextCoordinator" in source
    assert "class WorkspaceContextSnapshot" in source


def test_cockpit_data_builder_has_explicit_inputs_not_window_contract() -> None:
    source = _source("src/tutor_assistant/ui/teacher_cockpit_data.py")

    assert "class CockpitDataInputs" in source
    assert "def build_cockpit_snapshot(\n    inputs: CockpitDataInputs" in source
    assert "getattr(window" not in source
    assert "_safe_running_workers(window" not in source


def test_production_cockpit_refresh_uses_workspace_snapshot_and_timer_is_fallback() -> None:
    source = _source("src/tutor_assistant/ui/teacher_cockpit.py")
    sync_source = _source("src/tutor_assistant/ui/workspace_sync.py")

    assert "workspace_context_snapshot" in source
    assert "collect_cockpit_inputs(" in source
    assert "_build_cockpit_snapshot(" in source
    assert "_build_cockpit_snapshot(self.window" not in source
    assert "setInterval(30_000)" in source
    assert "cockpit.refresh(workspace=workspace)" in sync_source


def test_parallel_presentation_is_driven_by_typed_workspace_snapshot() -> None:
    source = _source("src/tutor_assistant/ui/workspace_sync.py")

    assert "WorkspaceContextCoordinator" in source
    assert "parallel_context_text(workspace)" in source
    assert "workspace.stop_lesson_id" not in source
    assert "workspace.recording_active and not workspace.recording_stopping" in source


def test_complete_production_root_owns_workspace_sync_adapter() -> None:
    source = _source("src/tutor_assistant/ui/recording_recovery_app.py")

    assert "WorkspaceSyncMixin" in source
    assert (
        "class MainWindow(WorkspaceSyncMixin, RecordingFinalizeMainWindow, ShutdownMainWindow)"
        in source
    )
