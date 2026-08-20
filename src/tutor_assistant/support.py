from __future__ import annotations

import json
import platform
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .config import AppConfig
from .diagnostics import run_diagnostics
from .logging_config import log_directory
from .recording import list_input_devices, list_system_audio_sources
from .runtime import build_identity
from .security.redaction import find_secret_matches, redact_text

SAFE_SESSION_FILES = {"session.json", "sync_report.json", "audio_quality_report.json"}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _safe_config(config: AppConfig) -> dict[str, object]:
    return {
        "setup_completed": config.setup_completed,
        "workspace": str(config.workspace),
        "recording": config.recording.model_dump(mode="json"),
        "whisper": config.whisper.model_dump(mode="json"),
        "latex": config.latex.model_dump(mode="json"),
        "content": config.content.model_dump(mode="json"),
        "normalization": {
            "provider": config.normalization.provider,
            "credential_source": config.normalization.credential_source,
            "cloud_policy": config.normalization.effective_cloud_policy,
            "yandex_folder_configured": bool(config.normalization.yandex_folder_id),
            "yandex_model": config.normalization.yandex_model,
        },
        "repository": {
            "base_branch": config.repository.base_branch,
            "push": config.repository.push,
            "auto_create_pr": config.repository.auto_create_pr,
            "repository_full_name": config.repository.repository_full_name,
        },
    }


def _safe_entry(name: str, value: str) -> str:
    redacted = redact_text(value)
    findings = find_secret_matches(redacted)
    if findings:
        raise RuntimeError(f"Support bundle заблокирован: секрет обнаружен в {name}")
    return redacted


def _safe_json_file(path: Path, *, fallback: dict[str, object]) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else fallback
    except (OSError, UnicodeError, json.JSONDecodeError):
        return fallback


def _workspace_health(config: AppConfig) -> dict[str, object]:
    database = config.workspace / "tutor-assistant.sqlite3"
    return {
        "database_exists": database.is_file(),
        "database_size_bytes": database.stat().st_size if database.is_file() else 0,
        "backups_present": sum(1 for _ in (config.workspace / "backups").glob("*.sqlite3")),
        "crash_marker_present": (config.workspace / "crash" / "last-crash.json").is_file(),
    }


def create_support_bundle(
    config: AppConfig,
    config_path: Path = Path("config/app.yaml"),
    output: Path | None = None,
) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = output or config.workspace / "support" / f"tutor-assistant-support-{timestamp}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        diagnostics = run_diagnostics(config, config_path).to_dict()
    except Exception as exc:
        diagnostics = {"ready": False, "collection_error": type(exc).__name__}
    try:
        inputs = list_input_devices()
        devices = {
            "microphones": [device.to_dict() for device in inputs],
            "system_audio": [
                source.to_dict()
                for source in list_system_audio_sources(inputs, config.recording.target_sample_rate)
            ],
        }
    except Exception as exc:
        devices = {"collection_error": type(exc).__name__}
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "application_version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "contains_audio": False,
        "contains_transcripts": False,
        "contains_secrets": False,
        "contains_cloud_payloads": False,
        "logs_redacted": True,
    }
    privacy_report = {
        "redaction_enabled": True,
        "secret_scan": "passed",
        "cloud_payloads_included": False,
        "credentials_included": False,
    }
    entries: dict[str, str] = {
        "manifest.json": _json(manifest),
        "environment.json": _json(diagnostics),
        "devices.json": _json(devices),
        "config-sanitized.json": _json(_safe_config(config)),
        "privacy-report.json": _json(privacy_report),
        "build-info.json": _json(build_identity().to_dict()),
        "backup-status.json": _json(
            _safe_json_file(
                config.workspace / "maintenance" / "backup-status.json",
                fallback={"available": False},
            )
        ),
        "workspace-health.json": _json(_workspace_health(config)),
    }
    crash_marker = config.workspace / "crash" / "last-crash.json"
    if crash_marker.is_file():
        from .crash import read_crash_marker

        marker = read_crash_marker(config.workspace)
        entries["crash/last-crash.json"] = _json(
            marker if marker is not None else {"available": False, "malformed": True}
        )
    logs = log_directory(config.workspace)
    if logs.exists():
        for path in sorted(logs.glob("application.log*")):
            entries[f"logs/{path.name}"] = path.read_text(encoding="utf-8", errors="replace")
    lesson_root = config.workspace / "lessons"
    binary_entries: list[tuple[Path, str]] = []
    if lesson_root.exists():
        candidates = sorted(
            (path for path in lesson_root.rglob("*.json") if path.name in SAFE_SESSION_FILES),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:15]
        for path in candidates:
            binary_entries.append(
                (path, f"recent-recordings/{path.parent.parent.name}/{path.name}")
            )
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, _safe_entry(name, value))
        for path, name in binary_entries:
            archive.writestr(
                name,
                _safe_entry(name, path.read_text(encoding="utf-8", errors="replace")),
            )
    return target.resolve()
