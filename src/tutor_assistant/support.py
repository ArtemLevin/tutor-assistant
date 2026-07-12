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
        "repository": {
            "base_branch": config.repository.base_branch,
            "push": config.repository.push,
            "auto_create_pr": config.repository.auto_create_pr,
            "repository_full_name": config.repository.repository_full_name,
        },
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
        diagnostics = {"ready": False, "collection_error": str(exc)}
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
        devices = {"collection_error": str(exc)}
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "application_version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "contains_audio": False,
        "contains_transcripts": False,
    }
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", _json(manifest))
        archive.writestr("environment.json", _json(diagnostics))
        archive.writestr("devices.json", _json(devices))
        archive.writestr("config-sanitized.json", _json(_safe_config(config)))
        logs = log_directory(config.workspace)
        if logs.exists():
            for path in sorted(logs.glob("application.log*")):
                archive.write(path, f"logs/{path.name}")
        lesson_root = config.workspace / "lessons"
        if lesson_root.exists():
            candidates = sorted(
                (path for path in lesson_root.rglob("*.json") if path.name in SAFE_SESSION_FILES),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[:15]
            for path in candidates:
                archive.write(path, f"recent-recordings/{path.parent.parent.name}/{path.name}")
    return target.resolve()
