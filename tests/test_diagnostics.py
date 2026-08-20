from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import tutor_assistant.diagnostics as diagnostics_module
import tutor_assistant.normalization as normalization_module
import tutor_assistant.recording as recording_module
from tutor_assistant.config import AppConfig
from tutor_assistant.diagnostics import (
    DiagnosticCheck,
    DiagnosticReport,
    format_diagnostics,
    run_diagnostics,
)
from tutor_assistant.latex.models import EnvironmentReport


def configured_environment(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    students = tmp_path / "students.yaml"
    students.write_text("students: []\n", encoding="utf-8")
    repository = tmp_path / "private-students"
    (repository / ".git").mkdir(parents=True)
    configuration_path = tmp_path / "app.yaml"
    config = AppConfig(setup_completed=True, workspace=workspace, students_file=students)
    config.repository.students_repo = repository
    config.latex.enabled = False
    config.save(configuration_path)
    original_find_spec = diagnostics_module.importlib.util.find_spec
    monkeypatch.setattr(
        diagnostics_module.importlib.util,
        "find_spec",
        lambda module: object() if module == "faster_whisper" else original_find_spec(module),
    )
    monkeypatch.setattr(
        diagnostics_module.shutil,
        "which",
        lambda command: None if command == "gh" else f"/usr/bin/{command}",
    )
    monkeypatch.setattr(recording_module, "list_input_devices", lambda: ["microphone"])
    monkeypatch.setattr(recording_module, "list_loopback_devices", lambda _rate: ["loopback"])
    provider_diagnostics = SimpleNamespace(
        reachable=True,
        endpoint_local=True,
        endpoint="http://127.0.0.1:11434",
        version="1.0",
        model_available=True,
        plain_text_valid=True,
        errors=[],
    )
    monkeypatch.setattr(
        normalization_module,
        "build_provider",
        lambda _config: SimpleNamespace(diagnose=lambda: provider_diagnostics),
    )
    return config, configuration_path, provider_diagnostics


def checks_by_name(report: DiagnosticReport) -> dict[str, DiagnosticCheck]:
    return {check.name: check for check in report.checks}


def test_report_counts_required_errors_and_warnings() -> None:
    report = DiagnosticReport(
        ready=False,
        checks=(
            DiagnosticCheck("Python", "ok", "3.13"),
            DiagnosticCheck("Git", "error", "missing"),
            DiagnosticCheck("FFmpeg", "warning", "missing", required=False),
        ),
    )

    assert report.errors == 1
    assert report.warnings == 1
    assert report.to_dict()["ready"] is False


def test_human_report_contains_summary() -> None:
    report = DiagnosticReport(
        ready=True,
        checks=(DiagnosticCheck("Python", "ok", "3.13"),),
    )

    output = format_diagnostics(report)

    assert "[OK] Python" in output
    assert "Итог: ГОТОВО" in output


def test_configured_local_environment_is_ready_with_verified_scheduled_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, config_path, _provider = configured_environment(tmp_path, monkeypatch)
    status_path = config.workspace / "maintenance" / "backup-status.json"
    status_path.parent.mkdir()
    status_path.write_text(
        json.dumps(
            {
                "verified": True,
                "last_successful_at": "2026-08-20T12:00:00Z",
                "next_due_at": "2026-08-21T12:00:00Z",
                "scheduled_copy_count": 3,
            }
        ),
        encoding="utf-8",
    )

    report = run_diagnostics(config, config_path)
    checks = checks_by_name(report)

    assert report.ready
    assert report.errors == 0
    assert checks["Аудиоустройства"].status == "ok"
    assert checks["WASAPI Loopback"].status == "ok"
    assert checks["Normalization endpoint"].status == "ok"
    assert checks["Normalization model"].status == "ok"
    assert checks["Normalization plain text"].status == "ok"
    assert checks["Automatic backup"].status == "ok"
    assert "copies: 3" in checks["Automatic backup"].message


@pytest.mark.parametrize("failure", ["input", "loopback", "provider"])
def test_external_device_or_provider_failure_is_reported_without_crashing(
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    config, config_path, _provider = configured_environment(tmp_path, monkeypatch)

    def unavailable(*_arguments):
        raise OSError(f"{failure} service unavailable")

    expected = {
        "input": "Аудиоустройства",
        "loopback": "WASAPI Loopback",
        "provider": "Нормализация",
    }[failure]
    if failure == "input":
        monkeypatch.setattr(recording_module, "list_input_devices", unavailable)
    elif failure == "loopback":
        monkeypatch.setattr(recording_module, "list_loopback_devices", unavailable)
    else:
        monkeypatch.setattr(normalization_module, "build_provider", unavailable)

    report = run_diagnostics(config, config_path)

    assert not report.ready
    failed = checks_by_name(report)[expected]
    assert failed.status == "error"
    assert failed.required
    assert f"{failure} service unavailable" in failed.message


def test_remote_provider_is_highlighted_as_privacy_warning(tmp_path: Path, monkeypatch) -> None:
    config, config_path, provider = configured_environment(tmp_path, monkeypatch)
    config.normalization.allow_remote_endpoint = True
    provider.endpoint_local = False
    provider.endpoint = "https://trusted-provider.example"

    report = run_diagnostics(config, config_path)
    endpoint = checks_by_name(report)["Normalization endpoint"]

    assert report.ready
    assert endpoint.status == "warning"
    assert not endpoint.required
    assert "транскрипт покинет этот компьютер" in endpoint.message


def test_unreachable_or_invalid_provider_is_a_blocking_configuration_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, config_path, provider = configured_environment(tmp_path, monkeypatch)
    provider.reachable = False
    provider.model_available = False
    provider.plain_text_valid = False
    provider.errors = ["provider timed out"]

    report = run_diagnostics(config, config_path)
    checks = checks_by_name(report)

    assert not report.ready
    assert checks["Normalization endpoint"].status == "error"
    assert "provider timed out" in checks["Normalization endpoint"].message
    assert checks["Normalization model"].status == "error"
    assert checks["Normalization plain text"].status == "error"


@pytest.mark.parametrize("status", ["missing", "malformed", "failed"])
def test_backup_diagnostics_warn_without_blocking_otherwise_ready_application(
    tmp_path: Path,
    monkeypatch,
    status: str,
) -> None:
    config, config_path, _provider = configured_environment(tmp_path, monkeypatch)
    path = config.workspace / "maintenance" / "backup-status.json"
    if status != "missing":
        path.parent.mkdir()
        path.write_text(
            "{invalid-json"
            if status == "malformed"
            else json.dumps({"verified": True, "last_error": "disk unavailable"}),
            encoding="utf-8",
        )

    report = run_diagnostics(config, config_path)
    backup = checks_by_name(report)["Automatic backup"]

    assert report.ready
    assert backup.status == "warning"
    assert not backup.required
    if status == "failed":
        assert "disk unavailable" in backup.message


def test_disabled_optional_components_are_reported_without_provider_requests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, config_path, _provider = configured_environment(tmp_path, monkeypatch)
    config.normalization.enabled = False
    config.content.backup_enabled = False

    def forbidden_provider(_configuration):
        raise AssertionError("disabled normalization unexpectedly contacted provider")

    monkeypatch.setattr(normalization_module, "build_provider", forbidden_provider)

    report = run_diagnostics(config, config_path)
    checks = checks_by_name(report)

    assert report.ready
    assert checks["Нормализация"].status == "ok"
    assert checks["Automatic backup"].status == "ok"
    assert "отключена" in checks["Нормализация"].message


def test_latex_environment_failure_and_missing_preview_are_actionable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import tutor_assistant.latex as latex_module

    config, config_path, _provider = configured_environment(tmp_path, monkeypatch)
    config.latex.enabled = True
    monkeypatch.setattr(
        latex_module,
        "inspect_latex_environment",
        lambda _configuration: EnvironmentReport(
            ready=False,
            latexmk=None,
            engine=None,
            pdftoppm=None,
            messages=["latexmk not installed"],
        ),
    )

    report = run_diagnostics(config, config_path)
    checks = checks_by_name(report)

    assert not report.ready
    assert checks["LaTeX"].status == "error"
    assert "latexmk not installed" in checks["LaTeX"].message
    assert checks["PDF-предпросмотр"].status == "warning"


def test_missing_configuration_and_student_file_block_readiness(tmp_path: Path, monkeypatch) -> None:
    config, config_path, _provider = configured_environment(tmp_path, monkeypatch)
    config_path.unlink()
    config.students_file.unlink()

    report = run_diagnostics(config, config_path)
    checks = checks_by_name(report)

    assert not report.ready
    assert checks["Конфигурация"].status == "error"
    assert checks["Список учеников"].status == "error"


def test_disk_usage_failure_is_nonblocking_warning(tmp_path: Path, monkeypatch) -> None:
    config, config_path, _provider = configured_environment(tmp_path, monkeypatch)

    def fail_disk_usage(_path: Path):
        raise OSError("disk statistics unavailable")

    monkeypatch.setattr(diagnostics_module.shutil, "disk_usage", fail_disk_usage)

    report = run_diagnostics(config, config_path)
    disk = checks_by_name(report)["Свободное место"]

    assert report.ready
    assert disk.status == "warning"
    assert not disk.required
    assert "disk statistics unavailable" in disk.message
