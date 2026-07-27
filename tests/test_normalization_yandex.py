from __future__ import annotations

import json

import pytest

import tutor_assistant.normalization.yandex_client as yandex_module
from tutor_assistant.config import NormalizationConfig
from tutor_assistant.normalization.errors import YandexAIStudioAuthenticationError
from tutor_assistant.normalization.models import NormalizationChunkRequest
from tutor_assistant.normalization.prompts import PROMPT_VERSION
from tutor_assistant.normalization.yandex_client import YandexAIStudioClient


class _Response:
    status_code = 200

    @staticmethod
    def _payload() -> dict:
        return {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": "[П] Решаем логарифмическое неравенство.",
                        }
                    ]
                }
            ]
        }

    @property
    def content(self) -> bytes:
        return json.dumps(self._payload(), ensure_ascii=False).encode("utf-8")

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload()


def _config() -> NormalizationConfig:
    return NormalizationConfig(
        provider="yandex_ai_studio",
        allow_cloud_processing=True,
        cloud_policy="ask_every_time",
        credential_source="environment",
        yandex_folder_id="folder-id",
    )


def test_yandex_client_uses_official_responses_api_and_api_key(monkeypatch) -> None:
    captured: dict = {}

    def request(method, url, *, headers, payload, **kwargs):
        captured.update(
            method=method,
            url=url,
            headers=headers,
            payload=payload,
            kwargs=kwargs,
        )
        return _Response()

    monkeypatch.setenv("YANDEX_AI_STUDIO_API_KEY", "secret")
    monkeypatch.setattr(yandex_module, "cancellable_request", request)
    client = YandexAIStudioClient(_config())
    response = client.normalize_chunk(
        NormalizationChunkRequest(
            lesson_id="synthetic",
            prompt_version=PROMPT_VERSION,
            mode="filter_only",
            segments=[
                {
                    "source_segment_id": 1,
                    "speaker": "П",
                    "text": "Решаем логарифмическое неравенство.",
                }
            ],
        )
    )

    assert response == "[П] Решаем логарифмическое неравенство."
    assert captured["method"] == "POST"
    assert captured["url"] == "https://ai.api.cloud.yandex.net/v1/responses"
    assert captured["headers"]["Authorization"] == "Api-Key secret"
    assert captured["headers"]["OpenAI-Project"] == "folder-id"
    assert captured["payload"]["model"] == "gpt://folder-id/yandexgpt-lite"
    assert captured["payload"]["temperature"] == 0
    assert "логарифм" in captured["payload"]["input"].casefold()
    assert captured["kwargs"]["follow_redirects"] is False
    assert captured["kwargs"]["trust_env"] is False


def test_yandex_client_requires_key_from_selected_credential_source(monkeypatch) -> None:
    monkeypatch.delenv("YANDEX_AI_STUDIO_API_KEY", raising=False)

    with pytest.raises(YandexAIStudioAuthenticationError, match="credential source"):
        YandexAIStudioClient(_config()).check_available()
