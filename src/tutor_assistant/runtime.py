"""Production runtime, build identity and application-session contracts."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from . import __version__

PRODUCTION_PYTHON = (3, 12)
COMPATIBILITY_PYTHONS = frozenset({(3, 13), (3, 14)})
APPLICATION_SESSION_ID = uuid4().hex


class RuntimeSupport(StrEnum):
    PRODUCTION = "production"
    COMPATIBILITY = "compatibility"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    major: int
    minor: int
    micro: int
    support: RuntimeSupport

    @property
    def version(self) -> str:
        return f"{self.major}.{self.minor}.{self.micro}"

    @property
    def production(self) -> bool:
        return self.support is RuntimeSupport.PRODUCTION

    @property
    def compatibility(self) -> bool:
        return self.support is RuntimeSupport.COMPATIBILITY

    @property
    def supported(self) -> bool:
        return self.support is not RuntimeSupport.UNSUPPORTED

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "support": self.support.value,
            "production": self.production,
            "compatibility": self.compatibility,
            "supported": self.supported,
        }


def inspect_runtime(version: tuple[int, ...] | None = None) -> RuntimeStatus:
    values = tuple(version or sys.version_info[:3])
    if len(values) < 2:
        raise ValueError("Python version must contain major and minor numbers")
    major, minor = values[:2]
    micro = values[2] if len(values) > 2 else 0
    pair = (major, minor)
    support = (
        RuntimeSupport.PRODUCTION
        if pair == PRODUCTION_PYTHON
        else RuntimeSupport.COMPATIBILITY
        if pair in COMPATIBILITY_PYTHONS
        else RuntimeSupport.UNSUPPORTED
    )
    return RuntimeStatus(major=major, minor=minor, micro=micro, support=support)


@dataclass(frozen=True, slots=True)
class BuildIdentity:
    application_version: str
    commit_sha: str
    release_channel: str
    python_version: str
    operating_system: str
    architecture: str
    frozen: bool
    application_session_id: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@lru_cache(maxsize=1)
def _source_commit_sha() -> str:
    if getattr(sys, "frozen", False):
        return "unknown"
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return completed.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _embedded_build_metadata() -> dict[str, object]:
    if not getattr(sys, "frozen", False):
        return {}
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    try:
        payload = json.loads((root / "build-info.json").read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def build_identity() -> BuildIdentity:
    embedded = _embedded_build_metadata()
    commit = (
        os.environ.get("TUTOR_ASSISTANT_BUILD_COMMIT")
        or str(embedded.get("commit", ""))
        or os.environ.get("GITHUB_SHA")
        or _source_commit_sha()
    )
    default_channel = "rc" if "rc" in __version__.lower() else "stable"
    channel = os.environ.get(
        "TUTOR_ASSISTANT_RELEASE_CHANNEL",
        str(embedded.get("release_channel", default_channel)),
    )
    if channel not in {"dev", "rc", "stable"}:
        channel = "dev"
    return BuildIdentity(
        application_version=__version__,
        commit_sha=commit,
        release_channel=channel,
        python_version=inspect_runtime().version,
        operating_system=platform.platform(),
        architecture=platform.machine(),
        frozen=bool(getattr(sys, "frozen", False)),
        application_session_id=APPLICATION_SESSION_ID,
    )
