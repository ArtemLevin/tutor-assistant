"""Reproducible Windows release assembly, smoke checks and privacy gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "windows" / "tutor-assistant.spec"
INSTALLER_SPEC = ROOT / "packaging" / "windows" / "TutorAssistant.iss"
FORBIDDEN_EXACT = frozenset({".env", "app.yaml", "students.yaml", "portable-secrets.json"})
FORBIDDEN_SUFFIXES = (".sqlite", ".sqlite3", ".db", ".wav", ".mp3", ".flac", ".m4a", ".pfx", ".pem")
FORBIDDEN_DIRECTORIES = frozenset({"data", "recordings", "lessons", "backups", "support"})
SCANNED_TEXT_SUFFIXES = frozenset({".yaml", ".yml", ".json", ".ini", ".cfg", ".toml", ".txt"})
MAX_SCANNED_TEXT_BYTES = 2 * 1024 * 1024


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as source:
        return str(tomllib.load(source)["project"]["version"])


def _canonical_version(value: str) -> str:
    return value.removeprefix("v").replace("-rc.", "rc").replace("-rc", "rc").lower()


def verify_version_consistency(tag: str | None = None) -> str:
    from tutor_assistant import __version__

    project = _project_version()
    expected = _canonical_version(project)
    candidates = {"package": __version__}
    if tag:
        candidates["git_tag"] = tag
    for label, value in candidates.items():
        if _canonical_version(value) != expected:
            raise RuntimeError(f"Version mismatch: pyproject={project}, {label}={value}")
    return project


def _forbidden_path(name: str) -> str | None:
    normalized = PurePosixPath(name.replace("\\", "/"))
    parts = tuple(part.lower() for part in normalized.parts)
    if normalized.is_absolute() or ".." in parts:
        return "unsafe archive entry path"
    if any(part in FORBIDDEN_DIRECTORIES for part in parts):
        return "private user-data directory"
    basename = normalized.name.lower()
    if basename in FORBIDDEN_EXACT:
        return "private configuration or environment file"
    if basename.endswith(FORBIDDEN_SUFFIXES):
        return "private database, recording or signing material"
    if "transcript" in basename and basename.endswith((".txt", ".json", ".vtt", ".srt")):
        return "private lesson transcript"
    return None


def _scan_names(entries: list[str], label: str) -> list[str]:
    violations: list[str] = []
    for entry in entries:
        reason = _forbidden_path(entry)
        if reason:
            violations.append(f"{label}: {entry} ({reason})")
    return violations


def _scan_text_content(name: str, content: bytes, label: str) -> list[str]:
    from tutor_assistant.security.redaction import find_secret_matches

    if PurePosixPath(name).suffix.lower() not in SCANNED_TEXT_SUFFIXES:
        return []
    if len(content) > MAX_SCANNED_TEXT_BYTES:
        return []
    if find_secret_matches(content.decode("utf-8", errors="replace")):
        return [f"{label}: {name} (credential or token detected in artifact contents)"]
    return []


def scan_artifacts(path: Path) -> list[str]:
    target = path.expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(target)
    if target.is_file() and zipfile.is_zipfile(target):
        with zipfile.ZipFile(target) as archive:
            violations = _scan_names(archive.namelist(), target.name)
            for entry in archive.infolist():
                if entry.is_dir() or entry.file_size > MAX_SCANNED_TEXT_BYTES:
                    continue
                if PurePosixPath(entry.filename).suffix.lower() in SCANNED_TEXT_SUFFIXES:
                    violations.extend(_scan_text_content(entry.filename, archive.read(entry), target.name))
            return violations
    if target.is_file():
        return _scan_names([target.name], target.name)
    files = [item for item in target.rglob("*") if item.is_file()]
    violations = _scan_names(
        [item.relative_to(target).as_posix() for item in files],
        target.name,
    )
    for candidate in files:
        if candidate.is_symlink():
            violations.append(f"{target.name}: {candidate.name} (symbolic links are forbidden)")
            continue
        if candidate.suffix.lower() in SCANNED_TEXT_SUFFIXES:
            violations.extend(
                _scan_text_content(
                    candidate.relative_to(target).as_posix(),
                    candidate.read_bytes(),
                    target.name,
                )
            )
        if candidate.suffix.lower() in {".zip", ".whl"}:
            violations.extend(scan_artifacts(candidate))
    return violations


def validate_packaging_contract(tag: str | None = None) -> str:
    version = verify_version_consistency(tag)
    if not SPEC.is_file() or not INSTALLER_SPEC.is_file():
        raise RuntimeError("PyInstaller and Inno Setup specifications must both be present")
    spec = SPEC.read_text(encoding="utf-8")
    installer = INSTALLER_SPEC.read_text(encoding="utf-8")
    if "windows_entrypoint.py" not in spec or "COLLECT(" not in spec:
        raise RuntimeError("Windows packaging must use the production entrypoint and onedir layout")
    if "portable.mode" not in installer or "[UninstallDelete]" in installer.split("; User configuration")[0]:
        raise RuntimeError("Installer must preserve user data and exclude the portable marker")
    return version


def _run(command: list[str], *, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _ensure_clean_checkout() -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status.strip():
        raise RuntimeError("Release builds require a clean checkout")


def _require_windows_runtime() -> None:
    from tutor_assistant.runtime import inspect_runtime

    if sys.platform != "win32":
        raise RuntimeError("Windows release artifacts can only be assembled on Windows")
    if not inspect_runtime().production:
        raise RuntimeError("Windows release builds require the production Python 3.12 runtime")


def repack_portable() -> Path:
    version = validate_packaging_contract()
    directory = ROOT / "dist" / "TutorAssistant"
    violations = scan_artifacts(directory)
    if violations:
        raise RuntimeError("Privacy scan failed:\n" + "\n".join(violations))
    archive_root = ROOT / "dist" / f"TutorAssistant-{version}-win64-portable"
    return Path(
        shutil.make_archive(
            str(archive_root),
            "zip",
            root_dir=directory.parent,
            base_dir=directory.name,
        )
    )


def build_portable(*, allow_dirty: bool = False) -> tuple[Path, Path]:
    from tutor_assistant.runtime import build_identity

    validate_packaging_contract()
    _require_windows_runtime()
    if not allow_dirty:
        _ensure_clean_checkout()
    metadata = ROOT / "build" / "build-info.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    identity = build_identity()
    metadata.write_text(
        json.dumps(
            {
                "version": identity.application_version,
                "commit": identity.commit_sha,
                "release_channel": identity.release_channel,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)])
    directory = ROOT / "dist" / "TutorAssistant"
    executable = directory / "TutorAssistant.exe"
    if not executable.is_file():
        raise RuntimeError("PyInstaller did not create the production executable")
    config = directory / "config"
    config.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "config" / "app.example.yaml", config / "app.example.yaml")
    (directory / "portable.mode").write_text("explicit-portable-mode\n", encoding="utf-8")
    archive = repack_portable()
    return executable, archive


def smoke_executable(executable: Path) -> dict[str, object]:
    result = subprocess.run(
        [str(executable.resolve()), "--release-smoke"],
        cwd=executable.resolve().parent,
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if not isinstance(payload, dict) or payload.get("application_version") != _project_version():
        raise RuntimeError("Frozen executable returned inconsistent build metadata")
    if not payload.get("frozen"):
        raise RuntimeError("Release smoke must execute the frozen binary")
    return payload


def build_installer() -> Path:
    version = validate_packaging_contract()
    _require_windows_runtime()
    compiler = shutil.which("ISCC.exe") or shutil.which("iscc")
    if compiler is None:
        candidates = (
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Inno Setup 6" / "ISCC.exe",
        )
        compiler = next((str(candidate) for candidate in candidates if candidate.is_file()), None)
    if compiler is None:
        raise RuntimeError("Inno Setup compiler ISCC.exe was not found in PATH")
    _run([compiler, f"/DAppVersion={version}", str(INSTALLER_SPEC)])
    installer = ROOT / "dist" / f"TutorAssistant-{version}-win64-setup.exe"
    if not installer.is_file():
        raise RuntimeError("Inno Setup did not create the expected installer")
    return installer


def smoke_installer(installer: Path) -> None:
    from platformdirs import user_config_dir, user_data_dir

    install_directory = ROOT / "dist" / "installer-smoke"
    command = [
        str(installer.resolve()),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        f"/DIR={install_directory}",
    ]
    token = uuid4().hex
    config_directory = Path(user_config_dir("TutorAssistant", appauthor=False, roaming=True))
    workspace_directory = Path(user_data_dir("TutorAssistant", appauthor=False))
    sentinels = [
        config_directory / f"release-smoke-{token}.config",
        workspace_directory / f"release-smoke-{token}.sqlite3",
        workspace_directory / "backups" / f"release-smoke-{token}.backup",
    ]
    try:
        _run(command)
        executable = install_directory / "TutorAssistant.exe"
        if (install_directory / "portable.mode").exists():
            raise RuntimeError("Installed application unexpectedly retained portable mode")
        for sentinel in sentinels:
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text(token, encoding="utf-8")
        smoke_executable(executable)
        _run(command)
        if not all(item.is_file() and item.read_text(encoding="utf-8") == token for item in sentinels):
            raise RuntimeError("Reinstall or upgrade modified user configuration, workspace, or backups")
        smoke_executable(executable)
        uninstaller = install_directory / "unins000.exe"
        if not uninstaller.is_file():
            raise RuntimeError("Installed application did not register its uninstaller")
        _run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        if not all(item.is_file() for item in sentinels):
            raise RuntimeError("Application uninstall deleted user configuration, workspace, or backups")
    finally:
        for sentinel in sentinels:
            sentinel.unlink(missing_ok=True)


def write_build_manifest(output: Path, *, signed: bool, tag: str | None = None) -> Path:
    version = verify_version_consistency(tag)
    payload = {
        "version": version,
        "commit": os.environ.get("TUTOR_ASSISTANT_BUILD_COMMIT", os.environ.get("GITHUB_SHA", "unknown")),
        "python": platform.python_version(),
        "platform": "windows-x64",
        "build_type": "release-candidate" if "rc" in version else "release",
        "signed": signed,
        "signing_exception": None if signed else "No code-signing certificate was configured",
        "created_at": datetime.now(UTC).isoformat(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def write_checksums(directory: Path, output: Path) -> Path:
    records: list[str] = []
    for candidate in sorted(directory.iterdir()):
        if not candidate.is_file() or candidate.resolve() == output.resolve():
            continue
        if candidate.suffix.lower() not in {".zip", ".exe", ".whl", ".json"}:
            continue
        with candidate.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        records.append(f"{digest}  {candidate.name}")
    output.write_text("\n".join(records) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--installer", action="store_true")
    parser.add_argument("--repack", action="store_true")
    parser.add_argument("--smoke", type=Path)
    parser.add_argument("--installer-smoke", type=Path)
    parser.add_argument("--scan", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--checksums", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--signed", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        print(f"Windows packaging contract OK: {validate_packaging_contract(args.tag)}")
    if args.build:
        executable, archive = build_portable(allow_dirty=args.allow_dirty)
        print(f"Executable: {executable}\nPortable archive: {archive}")
    if args.installer:
        print(build_installer())
    if args.repack:
        print(repack_portable())
    if args.smoke:
        print(json.dumps(smoke_executable(args.smoke), ensure_ascii=False, indent=2))
    if args.installer_smoke:
        smoke_installer(args.installer_smoke)
        print("Installer smoke passed")
    if args.scan:
        violations = scan_artifacts(args.scan)
        if violations:
            raise SystemExit("Privacy scan failed:\n" + "\n".join(violations))
        print(f"Artifact privacy scan passed: {args.scan}")
    if args.manifest:
        print(write_build_manifest(args.manifest, signed=args.signed, tag=args.tag))
    if args.checksums:
        print(write_checksums(args.checksums.parent, args.checksums))


if __name__ == "__main__":
    main()
