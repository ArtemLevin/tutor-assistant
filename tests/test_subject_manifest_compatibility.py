from __future__ import annotations

from datetime import UTC, datetime

from tutor_assistant.normalization.models import (
    NormalizationManifest,
    NormalizationRunStatus,
)


def test_legacy_manifest_defaults_to_generic_subject_profile() -> None:
    now = datetime.now(UTC)
    manifest = NormalizationManifest.model_validate(
        {
            "provider": "ollama",
            "model": "qwen3:8b",
            "prompt_version": "educational-content-filter.v1",
            "source_artifact": "00_raw_segments.json",
            "source_sha256": "a" * 64,
            "configuration_hash": "b" * 64,
            "started_at": now,
            "completed_at": now,
            "elapsed_seconds": 1.25,
            "chunk_count": 1,
            "attempts": 1,
            "status": NormalizationRunStatus.REVIEW_REQUIRED,
            "statistics": {
                "source_characters": 100,
                "normalized_characters": 80,
                "retained_ratio": 0.8,
                "source_segments": 3,
                "chunk_count": 1,
            },
            "quality": {
                "plain_text_valid": True,
                "numbers_preserved": True,
                "formula_tokens_preserved": True,
                "protected_content_preserved": True,
                "requires_manual_attention": False,
                "warnings": [],
            },
        }
    )

    assert manifest.lesson_subject == "generic"
    assert manifest.subject_profile == "generic"
    assert manifest.quality.subject_units_preserved is True
