from __future__ import annotations

import json
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from ..config import NormalizationConfig
from .errors import (
    InvalidStructuredOutputError,
    OllamaModelMissingError,
    OllamaTimeoutError,
    OllamaUnavailableError,
)
from .models import (
    NormalizationChunkRequest,
    NormalizationChunkResponse,
    OllamaDiagnostics,
)
from .prompts import SYSTEM_PROMPT, user_prompt
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
    ) -> httpx.Response:
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                timeout=timeout or self.config.request_timeout_seconds,
                trust_env=False,
            )
            response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError("Ollama не ответил за отведённое время") from exc
        except (httpx.HTTPError, OSError) as exc:
            raise OllamaUnavailableError(f"Ollama недоступен по адресу {self.base_url}") from exc

    def version(self) -> str:
        payload = self._request("GET", "/api/version", timeout=10).json()
        return str(payload.get("version") or "unknown")

    def list_models(self) -> list[str]:
        payload = self._request("GET", "/api/tags", timeout=15).json()
        names = []
        for item in payload.get("models", []):
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
        return names

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
    ) -> NormalizationChunkResponse:
        if cancellation:
            cancellation.raise_if_cancelled()
        payload = {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": NormalizationChunkResponse.model_json_schema(),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
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
        response = self._request("POST", "/api/chat", payload=payload)
        if cancellation:
            cancellation.raise_if_cancelled()
        try:
            body = response.json()
            content = body["message"]["content"]
            decoded = json.loads(content) if isinstance(content, str) else content
            return NormalizationChunkResponse.model_validate(decoded)
        except (KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            raise InvalidStructuredOutputError("Ollama вернул ответ, не соответствующий JSON Schema") from exc

    def diagnose(self) -> OllamaDiagnostics:
        host = urlsplit(self.base_url).hostname or ""
        endpoint_local = host.casefold() == "localhost"
        if not endpoint_local:
            try:
                endpoint_local = ip_address(host).is_loopback
            except ValueError:
                endpoint_local = False
        diagnostics = OllamaDiagnostics(
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
                prompt_version="transcript-normalizer.v1",
                mode="conservative",
                segments=[
                    {
                        "source_segment_id": 1,
                        "speaker": "П",
                        "text": "Сегодня решаем уравнение x + 2 = 5.",
                    }
                ],
            )
            result = self.normalize_chunk(synthetic)
            diagnostics.structured_output_valid = bool(result.decisions)
        except Exception as exc:
            diagnostics.errors.append(str(exc))
        return diagnostics
