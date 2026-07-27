from __future__ import annotations

import pytest

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.ui.normalization_provider import (
    provider_configuration_error,
    provider_hint,
    provider_label,
    provider_models,
    select_provider_config,
    with_provider_model,
)


def test_provider_catalog_exposes_local_and_yandex_choices() -> None:
    assert provider_label("ollama") == "Локальная LLM (Ollama)"
    assert provider_label("yandex_ai_studio") == "Yandex AI Studio"
    assert provider_models("ollama")[0] == "qwen3:8b"
    assert provider_models("yandex_ai_studio")[0] == "yandexgpt-lite"


def test_switch_to_yandex_requires_explicit_cloud_consent_and_folder() -> None:
    current = NormalizationConfig()

    with pytest.raises(ValueError, match="allow_cloud_processing"):
        select_provider_config(
            current,
            "yandex_ai_studio",
            folder_id="folder-id",
            allow_cloud_processing=False,
        )

    selected = select_provider_config(
        current,
        "yandex_ai_studio",
        folder_id="folder-id",
        allow_cloud_processing=True,
    )

    assert selected.provider == "yandex_ai_studio"
    assert selected.allow_cloud_processing is True
    assert selected.yandex_folder_id == "folder-id"


def test_provider_models_are_persisted_independently() -> None:
    current = NormalizationConfig()
    local = with_provider_model(current, "ollama", "qwen3:14b")
    cloud = select_provider_config(
        local,
        "yandex_ai_studio",
        folder_id="folder-id",
        allow_cloud_processing=True,
    )
    cloud = with_provider_model(cloud, "yandex_ai_studio", "yandexgpt")
    back_to_local = select_provider_config(cloud, "ollama")

    assert cloud.yandex_model == "yandexgpt"
    assert back_to_local.model == "qwen3:14b"
    assert back_to_local.effective_model == "qwen3:14b"


def test_yandex_readiness_uses_environment_without_persisting_key(monkeypatch) -> None:
    config = select_provider_config(
        NormalizationConfig(),
        "yandex_ai_studio",
        folder_id="folder-id",
        allow_cloud_processing=True,
    )
    monkeypatch.delenv(config.yandex_api_key_env, raising=False)
    assert config.yandex_api_key_env in (provider_configuration_error(config) or "")
    assert "задайте" in provider_hint(config)

    monkeypatch.setenv(config.yandex_api_key_env, "secret")
    assert provider_configuration_error(config) is None
    assert "ключ найден" in provider_hint(config)
