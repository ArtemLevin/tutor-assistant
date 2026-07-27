from __future__ import annotations

import os

from ..config import NormalizationConfig

PROVIDER_LABELS = {
    "ollama": "Локальная LLM (Ollama)",
    "yandex_ai_studio": "Yandex AI Studio",
}

PROVIDER_MODELS = {
    "ollama": ("qwen3:8b", "qwen3:14b"),
    "yandex_ai_studio": ("yandexgpt-lite", "yandexgpt"),
}


def provider_label(provider: str) -> str:
    try:
        return PROVIDER_LABELS[provider]
    except KeyError as exc:
        raise ValueError(f"Неизвестный провайдер LLM-фильтрации: {provider}") from exc


def provider_models(provider: str) -> tuple[str, ...]:
    try:
        return PROVIDER_MODELS[provider]
    except KeyError as exc:
        raise ValueError(f"Неизвестный провайдер LLM-фильтрации: {provider}") from exc


def select_provider_config(
    current: NormalizationConfig,
    provider: str,
    *,
    folder_id: str | None = None,
    allow_cloud_processing: bool | None = None,
) -> NormalizationConfig:
    payload = current.model_dump()
    payload["provider"] = provider
    if provider == "yandex_ai_studio":
        payload["allow_cloud_processing"] = (
            current.allow_cloud_processing if allow_cloud_processing is None else allow_cloud_processing
        )
        selected_folder = folder_id if folder_id is not None else current.yandex_folder_id
        payload["yandex_folder_id"] = (selected_folder or "").strip() or None
    return NormalizationConfig.model_validate(payload)


def with_provider_model(
    current: NormalizationConfig,
    provider: str,
    model: str,
) -> NormalizationConfig:
    selected = model.strip()
    if not selected:
        raise ValueError("Укажите модель LLM-фильтрации")
    payload = current.model_dump()
    payload["provider"] = provider
    if provider == "yandex_ai_studio":
        payload["yandex_model"] = selected
    elif provider == "ollama":
        payload["model"] = selected
    else:
        raise ValueError(f"Неизвестный провайдер LLM-фильтрации: {provider}")
    return NormalizationConfig.model_validate(payload)


def provider_configuration_error(config: NormalizationConfig) -> str | None:
    if config.provider != "yandex_ai_studio":
        return None
    if not config.allow_cloud_processing:
        return "Передача транскрипта в Yandex AI Studio не разрешена"
    if not (config.yandex_folder_id or "").strip():
        return "Не указан Yandex Cloud folder ID"
    if not os.getenv(config.yandex_api_key_env, "").strip():
        return (
            f"Не задана переменная окружения {config.yandex_api_key_env}. "
            "API-ключ не сохраняется в конфигурации приложения."
        )
    return None


def provider_hint(config: NormalizationConfig) -> str:
    if config.provider == "ollama":
        return "Локальная обработка: текст занятия не отправляется в облако."
    folder = (config.yandex_folder_id or "не указан").strip()
    key_ready = bool(os.getenv(config.yandex_api_key_env, "").strip())
    key_state = "ключ найден" if key_ready else f"задайте {config.yandex_api_key_env}"
    return f"Облачная обработка · folder: {folder} · {key_state}."
