from __future__ import annotations

import zipfile
from types import SimpleNamespace

from tutor_assistant.config import AppConfig
from tutor_assistant.support import create_support_bundle


def test_support_bundle_excludes_audio_and_transcripts(tmp_path, monkeypatch) -> None:
    config = AppConfig(workspace=tmp_path)
    recording = tmp_path / "lessons" / "lesson-1" / "recording"
    recording.mkdir(parents=True)
    (recording / "session.json").write_text('{"status": "recorded"}', encoding="utf-8")
    (recording / "lesson.wav").write_bytes(b"private audio")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "application.log").write_text("safe log", encoding="utf-8")
    monkeypatch.setattr(
        "tutor_assistant.support.run_diagnostics",
        lambda config, path: SimpleNamespace(to_dict=lambda: {"ready": True}),
    )
    monkeypatch.setattr("tutor_assistant.support.list_input_devices", lambda: [])
    monkeypatch.setattr("tutor_assistant.support.list_system_audio_sources", lambda *args: [])

    result = create_support_bundle(config, output=tmp_path / "support.zip")

    with zipfile.ZipFile(result) as archive:
        names = set(archive.namelist())
    assert "manifest.json" in names
    assert "logs/application.log" in names
    assert any(name.endswith("session.json") for name in names)
    assert all(not name.endswith(".wav") for name in names)
