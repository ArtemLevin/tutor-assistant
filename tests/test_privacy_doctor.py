from __future__ import annotations

from pathlib import Path

from tutor_assistant.config import AppConfig
from tutor_assistant.security.privacy_diagnostics import run_privacy_diagnostics


def test_privacy_doctor_checks_policy_redaction_and_migration(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    Path(".gitignore").write_text("config/app.yaml\ndata/\n.env\n", encoding="utf-8")
    config_path = Path("config/app.yaml")
    config_path.parent.mkdir()
    config_path.write_text(
        "normalization:\n  provider: ollama\n",
        encoding="utf-8",
    )
    config = AppConfig(workspace=Path("data"))

    report = run_privacy_diagnostics(config, config_path)

    assert report.ready is True
    names = {item.name for item in report.checks if item.ok}
    assert {"cloud_policy", "redaction_filter", "privacy_migration"} <= names
