from __future__ import annotations

from ..config import NormalizationConfig
from ..security.credentials import credential_status

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
        if payload["allow_cloud_processing"] and payload.get("cloud_policy") == "disabled":
            payload["cloud_policy"] = "ask_every_time"
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
    if config.effective_cloud_policy == "disabled":
        return "Передача транскрипта в Yandex AI Studio отключена"
    if not (config.yandex_folder_id or "").strip():
        return "Не указан Yandex Cloud folder ID"
    status = credential_status(config)
    if not status.configured:
        return (
            f"{status.detail}. API-ключ не сохраняется в YAML приложения; "
            f"совместимая переменная окружения: {config.yandex_api_key_env}."
        )
    return None


def provider_hint(config: NormalizationConfig) -> str:
    if config.provider == "ollama":
        return "Локальная обработка: текст занятия не отправляется в облако."
    folder = (config.yandex_folder_id or "не указан").strip()
    status = credential_status(config)
    key_state = "ключ найден" if status.configured else f"задайте ключ: {status.detail}"
    return (
        f"Облачная обработка · folder: {folder} · {key_state} · "
        f"политика: {config.effective_cloud_policy}."
    )
