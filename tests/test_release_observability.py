from __future__ import annotations

import json
import logging
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from tutor_assistant.config import AppConfig
from tutor_assistant.crash import ALLOWED_CRASH_FIELDS, crash_marker_path, read_crash_marker
from tutor_assistant.logging_config import (
    configure_logging,
    install_exception_hook,
    install_qt_message_handler,
)
from tutor_assistant.runtime import RuntimeSupport, build_identity, inspect_runtime
from tutor_assistant.support import create_support_bundle


@pytest.fixture
def restore_logging_hooks():
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    exception_hook = sys.excepthook
    thread_hook = threading.excepthook
    yield
    sys.excepthook = exception_hook
    threading.excepthook = thread_hook
    for handler in tuple(root.handlers):
        if handler not in handlers:
            handler.close()
    root.handlers = handlers
    root.setLevel(level)


@pytest.mark.parametrize(
    ("version", "support"),
    [
        ((3, 11, 9), RuntimeSupport.UNSUPPORTED),
        ((3, 12, 4), RuntimeSupport.PRODUCTION),
        ((3, 13, 0), RuntimeSupport.COMPATIBILITY),
        ((3, 14, 1), RuntimeSupport.COMPATIBILITY),
        ((3, 15, 0), RuntimeSupport.UNSUPPORTED),
    ],
)
def test_runtime_support_matrix(version, support) -> None:
    assert inspect_runtime(version).support is support


def test_build_identity_has_session_and_no_credentials(monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_ASSISTANT_BUILD_COMMIT", "abc123")
    monkeypatch.setenv("TUTOR_ASSISTANT_RELEASE_CHANNEL", "rc")

    identity = build_identity()

    assert identity.commit_sha == "abc123"
    assert identity.release_channel == "rc"
    assert len(identity.application_session_id) == 32
    assert "token" not in identity.to_dict()


def test_frozen_build_identity_reads_embedded_commit_without_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("TUTOR_ASSISTANT_BUILD_COMMIT", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.delenv("TUTOR_ASSISTANT_RELEASE_CHANNEL", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    (tmp_path / "build-info.json").write_text(
        json.dumps({"commit": "embedded-commit", "release_channel": "stable"}),
        encoding="utf-8",
    )

    identity = build_identity()

    assert identity.frozen
    assert identity.commit_sha == "embedded-commit"
    assert identity.release_channel == "stable"


def test_main_thread_crash_is_redacted_and_marker_is_allowlisted(
    tmp_path: Path,
    restore_logging_hooks,
) -> None:
    configure_logging(tmp_path)
    install_exception_hook(
        tmp_path,
        activity_provider=lambda: {"recording_active": True, "transcription_active": False},
    )
    secret = "exception-secret-value"

    sys.excepthook(RuntimeError, RuntimeError(f"Authorization: Api-Key {secret}"), None)

    marker = read_crash_marker(tmp_path)
    assert marker is not None
    assert set(marker) <= ALLOWED_CRASH_FIELDS
    assert marker["exception_type"] == "RuntimeError"
    assert marker["recording_active"] is True
    assert secret not in crash_marker_path(tmp_path).read_text(encoding="utf-8")
    log = (tmp_path / "logs" / "application.log").read_text(encoding="utf-8")
    assert secret not in log
    assert "[REDACTED]" in log
    assert f"session={marker['session_id']}" in log


def test_background_thread_crash_creates_safe_marker(
    tmp_path: Path,
    restore_logging_hooks,
) -> None:
    configure_logging(tmp_path)
    install_exception_hook(tmp_path)
    error = ValueError("api_key=thread-secret-value")
    arguments = threading.ExceptHookArgs((ValueError, error, None, threading.current_thread()))

    threading.excepthook(arguments)

    marker = read_crash_marker(tmp_path)
    assert marker is not None
    assert marker["component"] == "background-thread"
    assert "thread-secret-value" not in json.dumps(marker)


def test_qt_messages_are_redacted_before_logging(
    tmp_path: Path,
    restore_logging_hooks,
) -> None:
    from PySide6.QtCore import qInstallMessageHandler, qWarning

    configure_logging(tmp_path)
    install_qt_message_handler()
    try:
        qWarning("Authorization: Api-Key qt-secret-value")
    finally:
        qInstallMessageHandler(None)

    log = (tmp_path / "logs" / "application.log").read_text(encoding="utf-8")
    assert "qt-secret-value" not in log
    assert "[REDACTED]" in log


def test_support_bundle_v2_includes_only_safe_crash_and_backup_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = AppConfig(workspace=tmp_path / "workspace")
    crash = crash_marker_path(config.workspace)
    crash.parent.mkdir(parents=True)
    crash.write_text(
        json.dumps({"exception_type": "RuntimeError", "session_id": "safe", "transcript": "PRIVATE TEXT"}),
        encoding="utf-8",
    )
    status = config.workspace / "maintenance" / "backup-status.json"
    status.parent.mkdir(parents=True)
    status.write_text('{"verified": true, "scheduled_copy_count": 3}', encoding="utf-8")
    logs = config.workspace / "logs"
    logs.mkdir()
    (logs / "application.log").write_bytes(b"malformed-utf8:\xff Authorization: Api-Key secret-value")
    monkeypatch.setattr(
        "tutor_assistant.support.run_diagnostics",
        lambda *_args: type("Report", (), {"to_dict": lambda self: {"ready": True}})(),
    )
    monkeypatch.setattr("tutor_assistant.support.list_input_devices", lambda: [])
    monkeypatch.setattr("tutor_assistant.support.list_system_audio_sources", lambda *_args: [])

    target = create_support_bundle(config, output=tmp_path / "support.zip")

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        payloads = "\n".join(archive.read(name).decode("utf-8") for name in names)
        marker = json.loads(archive.read("crash/last-crash.json"))
    required = {"build-info.json", "backup-status.json", "workspace-health.json", "crash/last-crash.json"}
    assert required <= names
    assert "transcript" not in marker
    assert "PRIVATE TEXT" not in payloads
    assert "secret-value" not in payloads


def test_malformed_crash_marker_is_ignored(tmp_path: Path) -> None:
    path = crash_marker_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{malformed", encoding="utf-8")

    assert read_crash_marker(tmp_path) is None
