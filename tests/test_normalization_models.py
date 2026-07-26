from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import validate
from pydantic import ValidationError

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.normalization.artifacts import (
    configuration_hash,
    source_sha256,
    write_json_atomic,
)
from tutor_assistant.normalization.models import (
    NormalizationChunkResponse,
    NormalizedTranscript,
    SegmentDecision,
    SourceSegment,
)


def test_normalization_config_defaults_are_local_and_deterministic() -> None:
    config = NormalizationConfig()

    assert config.base_url == "http://127.0.0.1:11434"
    assert config.temperature == 0
    assert config.model == "qwen3:8b"
    assert config.require_manual_approval is True


@pytest.mark.parametrize(
    "base_url",
    (
        "http://192.168.1.10:11434",
        "https://ollama.example.test",
        "http://host.docker.internal:11434",
    ),
)
def test_remote_endpoint_is_rejected_by_default(base_url: str) -> None:
    with pytest.raises(ValidationError, match="Удалённый Ollama endpoint запрещён"):
        NormalizationConfig(base_url=base_url)


def test_remote_endpoint_requires_explicit_opt_in() -> None:
    config = NormalizationConfig(
        base_url="https://ollama.example.test",
        allow_remote_endpoint=True,
    )

    assert config.allow_remote_endpoint is True


def test_overlap_must_be_smaller_than_chunk() -> None:
    with pytest.raises(ValidationError, match="context_overlap_segments"):
        NormalizationConfig(
            max_segments_per_chunk=4,
            context_overlap_segments=4,
        )


def test_segment_decision_invariants() -> None:
    with pytest.raises(ValidationError, match="drop"):
        SegmentDecision(
            source_segment_id=1,
            action="drop",
            normalized_text="Нельзя",
            category="small_talk",
            reason_code="test",
        )
    with pytest.raises(ValidationError, match="trim"):
        SegmentDecision(
            source_segment_id=1,
            action="trim",
            normalized_text=" ",
            category="educational",
            reason_code="test",
        )


def test_chunk_response_exposes_json_schema() -> None:
    schema = NormalizationChunkResponse.model_json_schema()

    assert schema["type"] == "object"
    assert "decisions" in schema["properties"]
    assert "SegmentDecision" in schema["$defs"]


def test_source_and_configuration_hashes_are_stable() -> None:
    segments = [SourceSegment(source_segment_id=1, text="x + 2 = 5")]

    assert source_sha256(segments) == source_sha256([SourceSegment(source_segment_id=1, text="x + 2 = 5")])
    assert configuration_hash({"b": 2, "a": 1}) == configuration_hash({"a": 1, "b": 2})


def test_atomic_json_write_is_utf8_and_replaceable(tmp_path: Path) -> None:
    path = tmp_path / "result.json"

    write_json_atomic(path, {"text": "Привет", "version": 1})
    write_json_atomic(path, {"text": "Формула √x", "version": 2})

    text = path.read_text(encoding="utf-8")
    assert '"Формула √x"' in text
    assert '"version": 2' in text
    assert not tuple(tmp_path.glob("*.tmp"))


def test_committed_example_matches_pydantic_and_json_schema() -> None:
    root = Path(__file__).parents[1]
    payload = json.loads((root / "examples" / "transcript_normalized.json").read_text(encoding="utf-8"))
    schema = json.loads((root / "schemas" / "transcript-normalized.schema.json").read_text(encoding="utf-8"))

    NormalizedTranscript.model_validate(payload)
    validate(instance=payload, schema=schema)
