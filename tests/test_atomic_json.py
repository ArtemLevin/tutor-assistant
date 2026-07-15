import json
from pathlib import Path

import tutor_assistant.recording.recorder as recorder


def test_atomic_json_retries_windows_permission_error(tmp_path, monkeypatch) -> None:
    target = tmp_path / "session.json"
    target.write_text('{"status": "old"}', encoding="utf-8")
    real_replace = Path.replace
    calls = 0

    def flaky_replace(source: Path, destination: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(5, "Отказано в доступе", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(recorder, "sleep", lambda _seconds: None)

    recorder._atomic_json(target, {"status": "recorded"})

    assert calls == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "recorded"}
    assert not list(tmp_path.glob(".session.json.*.tmp"))


def test_atomic_json_falls_back_when_replace_stays_locked(tmp_path, monkeypatch) -> None:
    target = tmp_path / "session.json"
    target.write_text('{"status": "old"}', encoding="utf-8")

    def locked_replace(_source: Path, destination: Path) -> Path:
        raise PermissionError(5, "Отказано в доступе", str(destination))

    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr(recorder, "sleep", lambda _seconds: None)
    monkeypatch.setattr(recorder, "_ATOMIC_WRITE_ATTEMPTS", 3)

    recorder._atomic_json(target, {"status": "recorded", "chunks": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "status": "recorded",
        "chunks": 2,
    }
    assert not list(tmp_path.glob(".session.json.*.tmp"))
