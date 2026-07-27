from __future__ import annotations

import os

import pytest

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.normalization.models import NormalizationChunkRequest
from tutor_assistant.normalization.ollama_client import OllamaClient
from tutor_assistant.normalization.prompts import PROMPT_VERSION

pytestmark = pytest.mark.ollama


@pytest.mark.skipif(
    os.getenv("TUTOR_ASSISTANT_OLLAMA_TEST") != "1",
    reason="set TUTOR_ASSISTANT_OLLAMA_TEST=1 to run local Ollama tests",
)
def test_local_ollama_plain_text_output_is_repeatable() -> None:
    model = os.getenv("TUTOR_ASSISTANT_OLLAMA_MODEL", "qwen3:8b")
    client = OllamaClient(NormalizationConfig(model=model))
    client.check_available()
    request = NormalizationChunkRequest(
        lesson_id="synthetic-integration",
        prompt_version=PROMPT_VERSION,
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
    assert "x + 2 = 5" in first
    assert not first.lstrip().startswith("{")
