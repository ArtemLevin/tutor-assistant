from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol

from ..config import NormalizationConfig

YANDEX_CREDENTIAL_SERVICE = "TutorAssistant/YandexAIStudio"
ERROR_NOT_FOUND = 1168
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore(Protocol):
    def get_secret(self, service: str, account: str) -> str | None: ...

    def set_secret(self, service: str, account: str, value: str) -> None: ...

    def delete_secret(self, service: str, account: str) -> None: ...

    def is_available(self) -> bool: ...


class EnvironmentCredentialStore:
    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self.environ = os.environ if environ is None else environ

    def get_secret(self, service: str, account: str) -> str | None:
        del service
        return (self.environ.get(account) or "").strip() or None

    def set_secret(self, service: str, account: str, value: str) -> None:
        del service, account, value
        raise CredentialStoreError("Переменные окружения доступны только для чтения")

    def delete_secret(self, service: str, account: str) -> None:
        del service, account
        raise CredentialStoreError("Переменные окружения доступны только для чтения")

    def is_available(self) -> bool:
        return True


class MemoryCredentialStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_secret(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_secret(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def delete_secret(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)

    def is_available(self) -> bool:
        return True


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialStore:
    def __init__(self) -> None:
        self._api = ctypes.WinDLL("Advapi32.dll", use_last_error=True) if sys.platform == "win32" else None
        if self._api is not None:
            pointer = ctypes.POINTER(_CREDENTIALW)
            self._api.CredReadW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(pointer),
            ]
            self._api.CredReadW.restype = wintypes.BOOL
            self._api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
            self._api.CredWriteW.restype = wintypes.BOOL
            self._api.CredDeleteW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            self._api.CredDeleteW.restype = wintypes.BOOL
            self._api.CredFree.argtypes = [ctypes.c_void_p]
            self._api.CredFree.restype = None

    def is_available(self) -> bool:
        return self._api is not None

    @staticmethod
    def _target(service: str, account: str) -> str:
        return f"{service}/{account}"

    def _require_api(self):
        if self._api is None:
            raise CredentialStoreError("Windows Credential Manager доступен только в Windows")
        return self._api

    def get_secret(self, service: str, account: str) -> str | None:
        api = self._require_api()
        pointer = ctypes.POINTER(_CREDENTIALW)()
        if not api.CredReadW(
            self._target(service, account),
            CRED_TYPE_GENERIC,
            0,
            ctypes.byref(pointer),
        ):
            error = ctypes.get_last_error()
            if error == ERROR_NOT_FOUND:
                return None
            raise CredentialStoreError(ctypes.FormatError(error))
        try:
            credential = pointer.contents
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return raw.decode("utf-16-le").strip() or None
        finally:
            api.CredFree(pointer)

    def set_secret(self, service: str, account: str, value: str) -> None:
        api = self._require_api()
        secret = value.strip()
        if not secret:
            raise ValueError("API-ключ не может быть пустым")
        encoded = secret.encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        target = self._target(service, account)
        credential = _CREDENTIALW()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = account
        if not api.CredWriteW(ctypes.byref(credential), 0):
            error = ctypes.get_last_error()
            raise CredentialStoreError(ctypes.FormatError(error))

    def delete_secret(self, service: str, account: str) -> None:
        api = self._require_api()
        if api.CredDeleteW(self._target(service, account), CRED_TYPE_GENERIC, 0):
            return
        error = ctypes.get_last_error()
        if error != ERROR_NOT_FOUND:
            raise CredentialStoreError(ctypes.FormatError(error))


@dataclass(frozen=True)
class CredentialStatus:
    configured: bool
    source: str
    detail: str


def _credential_account(config: NormalizationConfig) -> str:
    return (config.yandex_folder_id or "default").strip() or "default"


def _system_store(store: CredentialStore | None = None) -> CredentialStore:
    return store or WindowsCredentialStore()


def credential_status(
    config: NormalizationConfig,
    *,
    environment: CredentialStore | None = None,
    system_store: CredentialStore | None = None,
) -> CredentialStatus:
    env_store = environment or EnvironmentCredentialStore()
    selected_system_store = _system_store(system_store)
    source = config.credential_source

    if source in {"auto", "environment"}:
        value = env_store.get_secret("environment", config.yandex_api_key_env)
        if value:
            return CredentialStatus(True, "environment", f"ключ найден в {config.yandex_api_key_env}")
        if source == "environment":
            return CredentialStatus(False, "missing", f"задайте {config.yandex_api_key_env}")

    if source in {"auto", "system_store"}:
        if not selected_system_store.is_available():
            return CredentialStatus(False, "unavailable", "системное хранилище credentials недоступно")
        value = selected_system_store.get_secret(
            YANDEX_CREDENTIAL_SERVICE,
            _credential_account(config),
        )
        if value:
            return CredentialStatus(True, "system_store", "ключ найден в Windows Credential Manager")
        return CredentialStatus(False, "missing", "сохраните API-ключ в системном хранилище")

    return CredentialStatus(False, "missing", "API-ключ не настроен")


def resolve_yandex_api_key(
    config: NormalizationConfig,
    *,
    environment: CredentialStore | None = None,
    system_store: CredentialStore | None = None,
) -> str:
    env_store = environment or EnvironmentCredentialStore()
    selected_system_store = _system_store(system_store)
    source = config.credential_source

    if source in {"auto", "environment"}:
        value = env_store.get_secret("environment", config.yandex_api_key_env)
        if value:
            return value
        if source == "environment":
            return ""

    if source in {"auto", "system_store"} and selected_system_store.is_available():
        return (
            selected_system_store.get_secret(
                YANDEX_CREDENTIAL_SERVICE,
                _credential_account(config),
            )
            or ""
        )
    return ""


def save_yandex_api_key(
    config: NormalizationConfig,
    value: str,
    *,
    system_store: CredentialStore | None = None,
) -> None:
    selected = _system_store(system_store)
    selected.set_secret(YANDEX_CREDENTIAL_SERVICE, _credential_account(config), value)


def delete_yandex_api_key(
    config: NormalizationConfig,
    *,
    system_store: CredentialStore | None = None,
) -> None:
    selected = _system_store(system_store)
    selected.delete_secret(YANDEX_CREDENTIAL_SERVICE, _credential_account(config))
