from __future__ import annotations

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.security.credentials import (
    EnvironmentCredentialStore,
    MemoryCredentialStore,
    credential_status,
    delete_yandex_api_key,
    resolve_yandex_api_key,
    save_yandex_api_key,
)


def _config(**updates) -> NormalizationConfig:
    payload = NormalizationConfig().model_dump()
    payload.update(
        {
            "provider": "yandex_ai_studio",
            "allow_cloud_processing": True,
            "yandex_folder_id": "folder-id",
        }
    )
    payload.update(updates)
    return NormalizationConfig.model_validate(payload)


def test_environment_credentials_keep_backward_compatibility() -> None:
    config = _config(credential_source="environment")
    environment = EnvironmentCredentialStore({config.yandex_api_key_env: "env-secret"})

    assert resolve_yandex_api_key(config, environment=environment) == "env-secret"
    assert credential_status(config, environment=environment).source == "environment"


def test_auto_prefers_environment_over_system_store() -> None:
    config = _config(credential_source="auto")
    environment = EnvironmentCredentialStore({config.yandex_api_key_env: "env-secret"})
    system = MemoryCredentialStore()
    save_yandex_api_key(config, "system-secret", system_store=system)

    assert (
        resolve_yandex_api_key(
            config,
            environment=environment,
            system_store=system,
        )
        == "env-secret"
    )


def test_system_store_save_status_and_delete() -> None:
    config = _config(credential_source="system_store")
    system = MemoryCredentialStore()

    assert not credential_status(config, system_store=system).configured
    save_yandex_api_key(config, "system-secret", system_store=system)
    assert credential_status(config, system_store=system).configured
    assert resolve_yandex_api_key(config, system_store=system) == "system-secret"

    delete_yandex_api_key(config, system_store=system)
    assert resolve_yandex_api_key(config, system_store=system) == ""
