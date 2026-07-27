from __future__ import annotations

from typing import Any

import httpx

from ..config import NormalizationConfig
from ..security.cloud_consent import CloudRequestEnvelope
from ..security.credentials import resolve_yandex_api_key
from .errors import (
    InvalidPlainTextOutputError,
    YandexAIStudioAuthenticationError,
    YandexAIStudioTimeoutError,
    YandexAIStudioUnavailableError,
)
from .http_client import cancellable_request
from .models import NormalizationChunkRequest, NormalizationDiagnostics
from .prompts import PROMPT_VERSION, system_prompt, user_prompt
from .protocol import CancellationToken


class YandexAIStudioClient:
    """Plain-text adapter for the Yandex AI Studio Responses API."""

    def __init__(self, config: NormalizationConfig, *, model: str | None = None) -> None:
        self.config = config
        self.model = model or config.yandex_model
        self.base_url = config.yandex_base_url.rstrip("/")
        self.folder_id = (config.yandex_folder_id or "").strip()

    @property
    def api_key(self) -> str:
        return resolve_yandex_api_key(self.config)

    @property
    def model_uri(self) -> str:
        if self.model.startswith("gpt://"):
            return self.model
        return f"gpt://{self.folder_id}/{self.model}"

    def check_available(self, model: str | None = None) -> None:
        del model
        if self.config.effective_cloud_policy == "disabled":
            raise YandexAIStudioUnavailableError(
                "Отправка в Yandex AI Studio отключена политикой cloud_policy"
            )
        if not self.folder_id:
            raise YandexAIStudioUnavailableError("Не указан Yandex Cloud folder ID")
        if not self.api_key:
            raise YandexAIStudioAuthenticationError(
                "API-ключ Yandex AI Studio не найден в выбранном credential source"
            )

    def _request(
        self,
        payload: dict[str, Any],
        *,
        cancellation: CancellationToken | None = None,
    ) -> httpx.Response:
        self.check_available()
        try:
            response = cancellable_request(
                "POST",
                f"{self.base_url}/responses",
                headers={
                    "Authorization": f"Api-Key {self.api_key}",
                    "Content-Type": "application/json",
                    "OpenAI-Project": self.folder_id,
                    "x-folder-id": self.folder_id,
                },
                payload=payload,
                timeout_seconds=self.config.request_timeout_seconds,
                trust_env=self.config.cloud_trust_env,
                follow_redirects=False,
                cancellation=cancellation,
            )
            if len(response.content) > self.config.cloud_max_response_bytes:
                raise InvalidPlainTextOutputError(
                    "Yandex AI Studio вернул ответ больше разрешённого размера"
                )
            return response
        except httpx.TimeoutException as exc:
            raise YandexAIStudioTimeoutError("Yandex AI Studio не ответил за отведённое время") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise YandexAIStudioAuthenticationError(
                    "Yandex AI Studio отклонил API-ключ или права сервисного аккаунта"
                ) from exc
            raise YandexAIStudioUnavailableError(
                f"Yandex AI Studio вернул HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise YandexAIStudioUnavailableError("Yandex AI Studio недоступен") from exc

    @staticmethod
    def _response_text(payload: dict[str, Any]) -> str:
        direct = payload.get("output_text")
        if isinstance(direct, str):
            return direct.strip()
        output = payload.get("output")
        if not isinstance(output, list):
            raise InvalidPlainTextOutputError("Yandex AI Studio не вернул output")
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "output_text"
                    and isinstance(part.get("text"), str)
                ):
                    parts.append(part["text"])
        if not parts:
            raise InvalidPlainTextOutputError("Yandex AI Studio не вернул текст ответа")
        return "\n".join(parts).strip()

    def normalize_chunk(
        self,
        request: NormalizationChunkRequest,
        *,
        validation_errors: tuple[str, ...] = (),
        cancellation: CancellationToken | None = None,
    ) -> str:
        if cancellation:
            cancellation.raise_if_cancelled()
        envelope = CloudRequestEnvelope.from_normalization_request(request)
        safe_request = envelope.as_normalization_request()
        prompt = (
            f"{system_prompt(safe_request.subject_profile)}\n\n"
            f"{user_prompt(safe_request, validation_errors=validation_errors)}"
        )
        response = self._request(
            {
                "model": self.model_uri,
                "input": prompt,
                "temperature": self.config.temperature,
                "max_output_tokens": self.config.num_predict,
            },
            cancellation=cancellation,
        )
        if cancellation:
            cancellation.raise_if_cancelled()
        try:
            payload = response.json()
        except ValueError as exc:
            raise InvalidPlainTextOutputError("Yandex AI Studio вернул невалидный ответ") from exc
        if not isinstance(payload, dict):
            raise InvalidPlainTextOutputError("Yandex AI Studio вернул ответ неизвестного формата")
        return self._response_text(payload)

    def diagnose(self) -> NormalizationDiagnostics:
        diagnostics = NormalizationDiagnostics(
            provider="yandex_ai_studio",
            endpoint=self.base_url,
            endpoint_local=False,
            reachable=False,
        )
        try:
            self.check_available()
            diagnostics.model_available = True
            synthetic = NormalizationChunkRequest(
                lesson_id="doctor-synthetic",
                prompt_version=PROMPT_VERSION,
                mode="filter_only",
                lesson_subject="mathematics",
                subject_profile="mathematics",
                segments=[
                    {
                        "source_segment_id": 1,
                        "speaker": "П",
                        "text": "Решаем уравнение x + 2 = 5.",
                    }
                ],
            )
            result = self.normalize_chunk(synthetic)
            diagnostics.reachable = True
            diagnostics.plain_text_valid = "x + 2 = 5" in result and not result.lstrip().startswith("{")
        except Exception as exc:
            diagnostics.errors.append(str(exc))
        return diagnostics
