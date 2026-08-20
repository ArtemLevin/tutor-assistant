from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tutor_assistant import __version__
from tutor_assistant.paths import application_paths

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("release_build_windows", ROOT / "scripts" / "build_windows.py")
assert SPEC is not None and SPEC.loader is not None
build_windows = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_windows)
SIGN_SPEC = importlib.util.spec_from_file_location(
    "release_sign_windows", ROOT / "scripts" / "sign_windows.py"
)
assert SIGN_SPEC is not None and SIGN_SPEC.loader is not None
sign_windows = importlib.util.module_from_spec(SIGN_SPEC)
SIGN_SPEC.loader.exec_module(sign_windows)


def test_release_version_and_tag_use_the_same_canonical_version() -> None:
    assert build_windows.validate_packaging_contract(f"v{__version__}") == __version__
    assert build_windows.verify_version_consistency("v1.0.0-rc.1") == __version__

    with pytest.raises(RuntimeError, match="Version mismatch"):
        build_windows.verify_version_consistency("v9.0.0")


@pytest.mark.parametrize(
    "private_path",
    [
        "TutorAssistant/config/app.yaml",
        "TutorAssistant/config/students.yaml",
        "TutorAssistant/.env",
        "TutorAssistant/data/archive.sqlite3",
        "TutorAssistant/lessons/private/lesson.wav",
        "TutorAssistant/transcript_verified.txt",
        "TutorAssistant/signing.pfx",
    ],
)
def test_privacy_scan_rejects_private_release_payloads(tmp_path: Path, private_path: str) -> None:
    archive = tmp_path / "portable.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(private_path, "private")

    assert build_windows.scan_artifacts(archive)


def test_privacy_scan_allows_example_config_and_production_code(tmp_path: Path) -> None:
    archive = tmp_path / "portable.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("TutorAssistant/config/app.example.yaml", "setup_completed: false")
        target.writestr("TutorAssistant/_internal/tutor_assistant/transcription.py", "pass")

    assert build_windows.scan_artifacts(archive) == []


def test_privacy_scan_detects_secret_hidden_in_an_example_config(tmp_path: Path) -> None:
    archive = tmp_path / "portable.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(
            "TutorAssistant/config/app.example.yaml",
            "api_key: accidentally-published-secret",
        )

    violations = build_windows.scan_artifacts(archive)

    assert violations
    assert "credential or token" in violations[0]


@pytest.mark.parametrize("entry", ["../../outside.json", "/absolute/private.json"])
def test_privacy_scan_rejects_archive_path_escape(tmp_path: Path, entry: str) -> None:
    archive = tmp_path / "portable.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(entry, "{}")

    assert any("unsafe archive entry" in item for item in build_windows.scan_artifacts(archive))


def test_installed_mode_separates_program_files_from_user_data(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "Programs" / "TutorAssistant" / "TutorAssistant.exe"
    executable.parent.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(
        "tutor_assistant.paths.user_config_dir",
        lambda *_args, **_kwargs: str(tmp_path / "Roaming"),
    )
    monkeypatch.setattr(
        "tutor_assistant.paths.user_data_dir",
        lambda *_args, **_kwargs: str(tmp_path / "Local"),
    )

    paths = application_paths()

    assert paths.mode == "installed"
    assert paths.configuration_directory == tmp_path / "Roaming"
    assert paths.workspace_directory == tmp_path / "Local"
    assert executable.parent not in paths.workspace_directory.parents


def test_portable_mode_requires_an_explicit_marker(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "TutorAssistant.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    (tmp_path / "portable.mode").write_text("explicit\n", encoding="utf-8")

    paths = application_paths()

    assert paths.mode == "portable"
    assert paths.workspace_directory == tmp_path / "data"


def test_build_manifest_declares_unsigned_artifacts_honestly(tmp_path: Path) -> None:
    manifest = build_windows.write_build_manifest(tmp_path / "build-manifest.json", signed=False)
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert payload["version"] == __version__
    assert payload["signed"] is False
    assert payload["signing_exception"]


def test_stable_required_gate_and_nonblocking_compatibility_are_separate() -> None:
    gate = (ROOT / ".github" / "workflows" / "release-gate.yml").read_text(encoding="utf-8")
    compatibility = (ROOT / ".github" / "workflows" / "python-compatibility.yml").read_text(encoding="utf-8")
    legacy = (ROOT / ".github" / "workflows" / "windows-content.yml").read_text(encoding="utf-8")

    assert "name: Release 1.0 Gate" in gate
    assert "if: always()" in gate
    assert "production_py312" in gate
    assert "'3.13', '3.14'" in compatibility
    assert "'3.11'" not in compatibility
    assert "'3.11'" not in legacy


def test_installer_excludes_portable_mode_and_has_no_user_data_deletion() -> None:
    installer = (ROOT / "packaging" / "windows" / "TutorAssistant.iss").read_text(encoding="utf-8")

    assert 'Excludes: "portable.mode"' in installer
    assert "DefaultDirName={localappdata}\\Programs\\TutorAssistant" in installer
    assert "[UninstallDelete]" not in installer.split("; User configuration")[0]


def test_frozen_entrypoint_supports_operational_cli_and_production_gui() -> None:
    source = (ROOT / "scripts" / "windows_entrypoint.py").read_text(encoding="utf-8")

    assert "tutor_assistant.ui.recording_recovery_app" in source
    assert "tutor_assistant.cli" in source
    assert '"recovery-drill"' in source
    assert '"hardware-soak"' in source


def test_unsigned_release_requires_explicit_exception(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WINDOWS_SIGNING_CERTIFICATE", raising=False)

    with pytest.raises(RuntimeError, match="signing certificate"):
        sign_windows.sign_and_verify([tmp_path / "app.exe"])

    assert sign_windows.sign_and_verify([tmp_path / "app.exe"], allow_unsigned=True) is False


def test_signing_failure_never_exposes_certificate_password(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WINDOWS_SIGNING_CERTIFICATE", "Y2VydGlmaWNhdGU=")
    monkeypatch.setenv("WINDOWS_SIGNING_PASSWORD", "private-signing-password")
    monkeypatch.setattr(sign_windows.shutil, "which", lambda _name: "signtool.exe")
    certificate_path: Path | None = None

    def fail(command, **_kwargs):
        nonlocal certificate_path
        certificate_path = Path(command[3])
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(sign_windows.subprocess, "run", fail)

    with pytest.raises(RuntimeError) as error:
        sign_windows.sign_and_verify([tmp_path / "app.exe"])

    assert "private-signing-password" not in str(error.value)
    assert certificate_path is not None
    assert not certificate_path.exists()
