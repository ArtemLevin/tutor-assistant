from __future__ import annotations

import os

import pytest

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.normalization.models import NormalizationChunkRequest
from tutor_assistant.normalization.ollama_client import OllamaClient

pytestmark = pytest.mark.ollama


@pytest.mark.skipif(
    os.getenv("TUTOR_ASSISTANT_OLLAMA_TEST") != "1",
    reason="set TUTOR_ASSISTANT_OLLAMA_TEST=1 to run local Ollama tests",
)
def test_local_ollama_structured_output_is_repeatable() -> None:
    model = os.getenv("TUTOR_ASSISTANT_OLLAMA_MODEL", "qwen3:8b")
    client = OllamaClient(NormalizationConfig(model=model))
    client.check_available()
    request = NormalizationChunkRequest(
        lesson_id="synthetic-integration",
        prompt_version="transcript-normalizer.v1",
        mode="conservative",
        segments=[
            {
                "source_segment_id": 1,
                "speaker": "П",
                "text": "Здравствуйте, меня слышно?",
            },
            {
                "source_segment_id": 2,
                "speaker": "П",
                "text": "Решаем уравнение x + 2 = 5.",
            },
        ],
    )

    first = client.normalize_chunk(request)
    second = client.normalize_chunk(request)

    assert first == second
    assert {item.source_segment_id for item in first.decisions} == {1, 2}
