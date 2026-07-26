from __future__ import annotations

import json

import httpx

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.normalization.models import NormalizationChunkRequest
from tutor_assistant.normalization.ollama_client import OllamaClient


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def test_ollama_client_sends_schema_and_disables_thinking(monkeypatch) -> None:
    requests: list[tuple[str, str, dict | None]] = []

    def request(method, url, *, json=None, **_kwargs):
        requests.append((method, url, json))
        if url.endswith("/api/tags"):
            return _Response({"models": [{"name": "qwen3:8b"}]})
        return _Response(
            {
                "message": {
                    "content": (
                        '{"decisions":[{"source_segment_id":1,"action":"keep",'
                        '"normalized_text":null,"category":"educational",'
                        '"reason_code":"test"}]}'
                    )
                }
            }
        )

    monkeypatch.setattr(httpx, "request", request)
    client = OllamaClient(NormalizationConfig())
    client.check_available()
    response = client.normalize_chunk(
        NormalizationChunkRequest(
            lesson_id="synthetic",
            prompt_version="transcript-normalizer.v1",
            mode="conservative",
            segments=[{"source_segment_id": 1, "text": "x + 2 = 5"}],
        )
    )

    assert response.decisions[0].source_segment_id == 1
    chat_payload = requests[-1][2]
    assert chat_payload is not None
    assert chat_payload["think"] is False
    assert chat_payload["stream"] is False
    assert chat_payload["options"]["temperature"] == 0
    assert chat_payload["format"]["type"] == "object"
    assert "x + 2 = 5" in chat_payload["messages"][1]["content"]
    json.dumps(chat_payload["format"])
