"""Allowlisted local crash markers; exception text never reaches the marker."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_text
from .runtime import build_identity

LOGGER = logging.getLogger(__name__)
ALLOWED_CRASH_FIELDS = frozenset(
    {
        "timestamp",
        "version",
        "session_id",
        "exception_type",
        "component",
        "recording_active",
        "transcription_active",
    }
)


def crash_marker_path(workspace: Path) -> Path:
    return workspace.expanduser().resolve() / "crash" / "last-crash.json"


def write_crash_marker(
    workspace: Path,
    *,
    exception_type: type[BaseException] | str,
    component: str,
    recording_active: bool = False,
    transcription_active: bool = False,
) -> Path:
    identity = build_identity()
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "version": identity.application_version,
        "session_id": identity.application_session_id,
        "exception_type": (
            exception_type.__name__ if isinstance(exception_type, type) else str(exception_type)
        ),
        "component": component,
        "recording_active": bool(recording_active),
        "transcription_active": bool(transcription_active),
    }
    path = crash_marker_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def read_crash_marker(workspace: Path) -> dict[str, Any] | None:
    path = crash_marker_path(workspace)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError):
        LOGGER.warning("Crash marker could not be decoded safely")
        return None
    if not isinstance(payload, Mapping):
        return None
    return {key: value for key, value in payload.items() if key in ALLOWED_CRASH_FIELDS}
