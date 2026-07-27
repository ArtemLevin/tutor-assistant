from __future__ import annotations

import os

import pytest

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.normalization.models import NormalizationChunkRequest
from tutor_assistant.normalization.prompts import PROMPT_VERSION
from tutor_assistant.normalization.yandex_client import YandexAIStudioClient

pytestmark = pytest.mark.yandex


@pytest.mark.skipif(
    os.getenv("TUTOR_ASSISTANT_YANDEX_TEST") != "1",
    reason="set TUTOR_ASSISTANT_YANDEX_TEST=1 to run Yandex AI Studio tests",
)
def test_yandex_ai_studio_returns_plain_russian_text() -> None:
    folder_id = os.environ["YANDEX_FOLDER_ID"]
    model = os.getenv("TUTOR_ASSISTANT_YANDEX_MODEL", "yandexgpt-lite")
    config = NormalizationConfig(
        provider="yandex_ai_studio",
        allow_cloud_processing=True,
        yandex_folder_id=folder_id,
        yandex_model=model,
    )
    client = YandexAIStudioClient(config)
    result = client.normalize_chunk(
        NormalizationChunkRequest(
            lesson_id="synthetic-integration",
            prompt_version=PROMPT_VERSION,
            mode="conservative",
            segments=[
                {
                    "source_segment_id": 1,
                    "speaker": "П",
                    "text": "Решаем логарифмическое неравенство log₂(x) > 3.",
                }
            ],
        )
    )

    assert "логарифмическое неравенство" in result.casefold()
    assert "log₂(x) > 3" in result
    assert not result.lstrip().startswith("{")
