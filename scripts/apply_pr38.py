# This file is generated once for PR 38 bootstrap.
FILES = {
    "src/tutor_assistant/security/__init__.py": '''
from .cloud_consent import (
    CloudConsentReceipt,
    CloudConsentScope,
    CloudConsentSession,
    CloudProcessingRequest,
    CloudRequestEnvelope,
    validate_cloud_consent,
)
from .credentials import (
    CredentialStatus,
    EnvironmentCredentialStore,
    MemoryCredentialStore,
    WindowsCredentialStore,
    credential_status,
    delete_yandex_api_key,
    resolve_yandex_api_key,
    save_yandex_api_key,
)
from .redaction import (
    RedactingFormatter,
    SensitiveDataFilter,
    find_secret_matches,
    redact_object,
    redact_sensitive_text,
)

__all__ = [
    "CloudConsentReceipt",
    "CloudConsentScope",
    "CloudConsentSession",
    "CloudProcessingRequest",
    "CloudRequestEnvelope",
    "CredentialStatus",
    "EnvironmentCredentialStore",
    "MemoryCredentialStore",
    "RedactingFormatter",
    "SensitiveDataFilter",
    "WindowsCredentialStore",
    "credential_status",
    "delete_yandex_api_key",
    "find_secret_matches",
    "redact_object",
    "redact_sensitive_text",
    "resolve_yandex_api_key",
    "save_yandex_api_key",
    "validate_cloud_consent",
]
''',
    "src/tutor_assistant/security/credentials.py": '''
from __future__ import annotations

import ctypes
import functools
import os
import sys
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

SERVICE_NAME = "tutor-assistant/yandex-ai-studio"
DEBAULT_ACCOUNT = "api-key"


#runtime_checkable
class CredentialStore(Protocol):
    def get_secret(self, service: str, account: str) -> str | None: ...
    def set_secret(self, service: str, account: str, value: str) -> None: ...
    def delete_secret(self, service: str, account: str) -> None: ...
    def is_available(self) -> bool: ...


class EnvironmentCredentialStore:
    def __init__(self, env_name: str) -> None:
        self.env_name = env_name

    def get_secret(self, service: str, account: str) -> str | None:
        del service, account
        return os.getenv(self.env_name, "").strip() or None

    def set_secret(self, service: str, account: str, value: str) -> None:
        del service, account, value
        raise RuntimeError("Переменная окружения доступна только для чтения")

    def delete_secret(self, service: str, account: str) -> None:
        del service, account
        raise RuntimeError("Переменная окружения доступна только для чтения")

    def is_available(self) -> bool:
        return bool(os.getenv(self.env_name, "").strip())


class MemoryCredentialStore:
    def __init__(self) -> None:
        self._secrets: dict[tuple[str, str], str] = {}

    def get_secret(self, service: str, account: str) -> str | None:
        return self._secrets.get((service, account))

    def set_secret(self, service: str, account: str, value: str) -> None:
        self._secrets[(service, account)] = value

    def delete_secret(self, service: str, account: str) -> None:
        self._secrets.pop((service, account), None)

    def is_available(self) -> bool:
        return True


if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
    DWORD = ctypes.c_ulong
    LPWCTSTR = ctypes.c_w_char_p
    LPBYTE = ctypes.POINTER(ctypes.c_ubyte)

    class Credential