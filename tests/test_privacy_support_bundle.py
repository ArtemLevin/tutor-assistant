from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tutor_assistant.config import AppConfig
from tutor_assistant.support import create_support_bundle


def test_support_bundle_redacts_logs_and_declares_privacy(monkeypatch, tmp_path: Path) -> None:
    config = AppConfig(workspace=tmp_path / "data")
    logs = config.workspace / "logs"
    logs.mkdir(parents=True)
    (logs / "application.log").write_text(
        "Authorization: Api-Key top-secret-value\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "tutor_assistant.support.run_diagnostics",
        lambda *_args, **_kwargs: type("Report", (), {"to_dict": lambda self: {"ready": True}})(),
    )
    monkeypatch.setattr("tutor_assistant.support.list_input_devices", lambda: [])
    monkeypatch.setattr(
        "tutor_assistant.support.list_system_audio_sources",
        lambda *_args, **_kwargs: [],
    )

    archive_path = create_support_bundle(config, output=tmp_path / "support.zip")

    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        log = archive.read("logs/application.log").decode("utf-8")
        privacy = json.loads(archive.read("privacy-report.json"))
    assert manifest["contains_secrets"] is False
    assert manifest["logs_redacted"] is True
    assert privacy["secret_scan"] == "passed"
    assert "top-secret-value" not in log
    assert "[REDACTED]" in log
