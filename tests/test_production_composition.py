"""Architecture gates for the explicit production GUI composition root."""

from __future__ import annotations

import inspect
from pathlib import Path

import tutor_assistant.ui.app as base_app
import tutor_assistant.ui.audio_resilient_app as audio_resilient_app
import tutor_assistant.ui.concurrent_app as concurrent_app
import tutor_assistant.ui.recording_finalize_app as recording_finalize_app
import tutor_assistant.ui.recording_recovery_app as recording_recovery_app
import tutor_assistant.ui.shutdown_app as shutdown_app
import tutor_assistant.ui.transcript_publication_app as transcript_publication_app

COMPOSITION_MODULES = (
    concurrent_app,
    shutdown_app,
    transcript_publication_app,
    audio_resilient_app,
    recording_finalize_app,
    recording_recovery_app,
)


def test_base_bootstrap_accepts_explicit_window_type() -> None:
    signature = inspect.signature(base_app.main)
    parameter = signature.parameters["window_type"]
    source = inspect.getsource(base_app.main)

    assert parameter.default is base_app.MainWindow
    assert "window = window_type(config_path)" in source
    assert "window = MainWindow(config_path)" not in source


def test_production_layers_use_explicit_bootstrap_without_global_rebinding() -> None:
    for module in COMPOSITION_MODULES:
        source = Path(module.__file__).read_text(encoding="utf-8")
        main_source = inspect.getsource(module.main)

        assert "base_app.MainWindow = MainWindow" not in source
        assert "base_app.main(MainWindow)" in main_source


def test_production_mro_contains_only_responsibility_bearing_layers() -> None:
    production = recording_recovery_app.MainWindow
    expected_prefix = (
        recording_recovery_app.MainWindow,
        recording_finalize_app.MainWindow,
        audio_resilient_app.MainWindow,
        transcript_publication_app.MainWindow,
        shutdown_app.MainWindow,
        concurrent_app.MainWindow,
        base_app.MainWindow,
    )

    assert production.__mro__[: len(expected_prefix)] == expected_prefix

    responsibilities = (
        (concurrent_app.MainWindow, "_sync_parallel_review_ui"),
        (shutdown_app.MainWindow, "closeEvent"),
        (transcript_publication_app.MainWindow, "publish"),
        (audio_resilient_app.MainWindow, "start_recording"),
        (recording_finalize_app.MainWindow, "_stop_recording_async"),
        (recording_recovery_app.MainWindow, "_offer_recovery"),
    )
    for window_type, method_name in responsibilities:
        assert method_name in window_type.__dict__


def test_console_entrypoint_remains_stable_at_complete_composition_root() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert (
        'tutor-assistant-gui = "tutor_assistant.ui.recording_recovery_app:main"'
        in pyproject
    )


def test_no_temporary_composition_migration_files_remain() -> None:
    assert not Path("scripts/_migrate_wave2_composition.py").exists()
    assert not Path(".github/workflows/_wave2_composition_migration.yml").exists()
    assert not Path("scripts/_migrate_shutdown_coordinator.py").exists()
    assert not Path(".github/workflows/_shutdown_coordinator_migration.yml").exists()
