from __future__ import annotations

from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import NormalizationConfig
from .errors import (
    InvalidPlainTextOutputError,
    OllamaModelMissingError,
    OllamaTimeoutError,
    OllamaUnavailableError,
)
from .http_client import cancellable_request
from .models import NormalizationChunkRequest, NormalizationDiagnostics
from .prompts import PROMPT_VERSION, system_prompt, user_prompt
from .protocol import CancellationToken


class OllamaClient:
    def __init__(self, config: NormalizationConfig, *, model: str | None = None) -> None:
        self.config = config
        self.model = model or config.model
        self.base_url = config.base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
        cancellation: CancellationToken | None = None,
    ) -> httpx.Response:
        try:
            return cancellable_request(
                method,
                f"{self.base_url}{path}",
                payload=payload,
                timeout_seconds=timeout or self.config.request_timeout_seconds,
                trust_env=False,
                cancellation=cancellation,
            )
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError("Ollama не ответил за отведённое время") from exc
        except httpx.HTTPStatusError as exc:
            raise OllamaUnavailableError(
                f"Ollama вернул HTTP {exc.response.status_code} по адресу {self.base_url}"
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise OllamaUnavailableError(f"Ollama недоступен по адресу {self.base_url}") from exc

    def version(self) -> str:
        payload = self._request("GET", "/api/version", timeout=10).json()
        return str(payload.get("version") or "unknown")

    def list_models(self) -> list[str]:
        payload = self._request("GET", "/api/tags", timeout=15).json()
        return [
            str(item["name"])
            for item in payload.get("models", [])
            if isinstance(item, dict) and item.get("name")
        ]

    def check_available(self, model: str | None = None) -> None:
        selected = model or self.model
        models = self.list_models()
        base_names = {name.split(":")[0] for name in models}
        if selected not in models and selected.split(":")[0] not in base_names:
            raise OllamaModelMissingError(f"Модель {selected} не найдена. Выполните: ollama pull {selected}")

    def normalize_chunk(
        self,
        request: NormalizationChunkRequest,
        *,
        validation_errors: tuple[str, ...] = (),
        cancellation: CancellationToken | None = None,
    ) -> str:
        if cancellation:
            cancellation.raise_if_cancelled()
        payload = {
            "model": self.model,
            "stream": False,
            "think": False,
            "messages": [
                {"role": "system", "content": system_prompt(request.subject_profile)},
                {
                    "role": "user",
                    "content": user_prompt(request, validation_errors=validation_errors),
                },
            ],
            "options": {
                "temperature": self.config.temperature,
                "num_ctx": self.config.num_ctx,
                "num_predict": self.config.num_predict,
                "seed": 0,
            },
        }
        response = self._request("POST", "/api/chat", payload=payload, cancellation=cancellation)
        if cancellation:
            cancellation.raise_if_cancelled()
        try:
            content = response.json()["message"]["content"]
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidPlainTextOutputError("Ollama не вернул текст ответа") from exc
        if not isinstance(content, str):
            raise InvalidPlainTextOutputError("Ollama вернул ответ неизвестного формата")
        return content.strip()

    def diagnose(self) -> NormalizationDiagnostics:
        host = urlsplit(self.base_url).hostname or ""
        endpoint_local = host.casefold() == "localhost"
        if not endpoint_local:
            try:
                endpoint_local = ip_address(host).is_loopback
            except ValueError:
                endpoint_local = False
        diagnostics = NormalizationDiagnostics(
            provider="ollama",
            endpoint=self.base_url,
            endpoint_local=endpoint_local,
            reachable=False,
        )
        try:
            diagnostics.version = self.version()
            diagnostics.reachable = True
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
            diagnostics.plain_text_valid = "x + 2 = 5" in result and not result.lstrip().startswith("{")
        except Exception as exc:
            diagnostics.errors.append(str(exc))
        return diagnostics
