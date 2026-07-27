from __future__ import annotations

import httpx

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.normalization.models import NormalizationChunkRequest
from tutor_assistant.normalization.ollama_client import OllamaClient
from tutor_assistant.normalization.prompts import PROMPT_VERSION


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def test_ollama_client_requests_plain_text_and_disables_thinking(monkeypatch) -> None:
    requests: list[tuple[str, str, dict | None]] = []

    def request(method, url, *, json=None, **_kwargs):
        requests.append((method, url, json))
        if url.endswith("/api/tags"):
            return _Response({"models": [{"name": "qwen3:8b"}]})
        return _Response({"message": {"content": "[П] Решаем x + 2 = 5."}})

    monkeypatch.setattr(httpx, "request", request)
    client = OllamaClient(NormalizationConfig())
    client.check_available()
    response = client.normalize_chunk(
        NormalizationChunkRequest(
            lesson_id="synthetic",
            prompt_version=PROMPT_VERSION,
            mode="conservative",
            segments=[{"source_segment_id": 1, "speaker": "П", "text": "Решаем x + 2 = 5."}],
        )
    )

    assert response == "[П] Решаем x + 2 = 5."
    chat_payload = requests[-1][2]
    assert chat_payload is not None
    assert chat_payload["think"] is False
    assert chat_payload["stream"] is False
    assert chat_payload["options"]["temperature"] == 0
    assert "format" not in chat_payload
    assert "обычный нормализованный текст" in chat_payload["messages"][0]["content"]
    assert "x + 2 = 5" in chat_payload["messages"][1]["content"]
