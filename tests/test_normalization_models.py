from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.normalization.artifacts import (
    configuration_hash,
    source_sha256,
    write_json_atomic,
    write_text_atomic,
)
from tutor_assistant.normalization.models import NormalizationChunkRequest, SourceSegment
from tutor_assistant.normalization.prompts import PROMPT_VERSION, SYSTEM_PROMPT


def test_normalization_config_defaults_are_local_and_deterministic() -> None:
    config = NormalizationConfig()

    assert config.base_url == "http://127.0.0.1:11434"
    assert config.provider == "ollama"
    assert config.temperature == 0
    assert config.effective_model == "qwen3:8b"
    assert config.allow_cloud_processing is False
    assert config.require_manual_approval is True


@pytest.mark.parametrize(
    "base_url",
    (
        "http://192.168.1.10:11434",
        "https://ollama.example.test",
        "http://host.docker.internal:11434",
    ),
)
def test_remote_ollama_endpoint_is_rejected_by_default(base_url: str) -> None:
    with pytest.raises(ValidationError, match="Удалённый Ollama endpoint запрещён"):
        NormalizationConfig(base_url=base_url)


def test_remote_ollama_endpoint_requires_explicit_opt_in() -> None:
    config = NormalizationConfig(
        base_url="https://ollama.example.test",
        allow_remote_endpoint=True,
    )

    assert config.allow_remote_endpoint is True


def test_yandex_provider_requires_explicit_cloud_opt_in_and_folder() -> None:
    with pytest.raises(ValidationError, match="allow_cloud_processing"):
        NormalizationConfig(provider="yandex_ai_studio", yandex_folder_id="folder")
    with pytest.raises(ValidationError, match="yandex_folder_id"):
        NormalizationConfig(provider="yandex_ai_studio", allow_cloud_processing=True)

    config = NormalizationConfig(
        provider="yandex_ai_studio",
        allow_cloud_processing=True,
        yandex_folder_id="folder",
    )
    assert config.effective_model == "yandexgpt-lite"


def test_yandex_endpoint_is_pinned_to_official_https_api() -> None:
    with pytest.raises(ValidationError, match="официальным"):
        NormalizationConfig(yandex_base_url="https://example.test/v1")


def test_overlap_must_be_smaller_than_chunk() -> None:
    with pytest.raises(ValidationError, match="context_overlap_segments"):
        NormalizationConfig(
            max_segments_per_chunk=4,
            context_overlap_segments=4,
        )


def test_plain_text_request_has_no_response_schema() -> None:
    request = NormalizationChunkRequest(
        lesson_id="lesson",
        prompt_version=PROMPT_VERSION,
        mode="conservative",
        segments=[SourceSegment(source_segment_id=1, text="Логарифмы")],
    )

    assert "response" not in request.model_dump()
    assert PROMPT_VERSION == "educational-content-filter.mathematics.v2"
    assert "логариф" in SYSTEM_PROMPT.casefold()
    assert "неравен" in SYSTEM_PROMPT.casefold()
    assert "JSON" in SYSTEM_PROMPT


def test_source_and_configuration_hashes_are_stable() -> None:
    segments = [SourceSegment(source_segment_id=1, text="x + 2 = 5")]

    assert source_sha256(segments) == source_sha256([SourceSegment(source_segment_id=1, text="x + 2 = 5")])
    assert configuration_hash({"b": 2, "a": 1}) == configuration_hash({"a": 1, "b": 2})


def test_atomic_text_and_manifest_writes_are_utf8_and_replaceable(tmp_path: Path) -> None:
    text_path = tmp_path / "result.txt"
    manifest_path = tmp_path / "manifest.json"

    write_text_atomic(text_path, "Привет")
    write_text_atomic(text_path, "Формула √x")
    write_json_atomic(manifest_path, {"version": 2})

    assert text_path.read_text(encoding="utf-8") == "Формула √x\n"
    assert '"version": 2' in manifest_path.read_text(encoding="utf-8")
    assert not tuple(tmp_path.glob("*.tmp"))


def test_committed_example_is_plain_utf8_text() -> None:
    root = Path(__file__).parents[1]
    text = (root / "examples" / "transcript_normalized.txt").read_text(encoding="utf-8")

    assert "[П]" in text
    assert "метод интервалов" in text.casefold()
    assert not text.lstrip().startswith(("{", "[{"))
