from __future__ import annotations

import pytest

from tutor_assistant.normalization.errors import (
    IncompleteSegmentClassificationError,
    InvalidStructuredOutputError,
    UnsafeNormalizationResultError,
)
from tutor_assistant.normalization.models import (
    NormalizationChunkResponse,
    SegmentDecision,
    SourceSegment,
)
from tutor_assistant.normalization.validation import (
    ValidationState,
    extract_formula_tokens,
    extract_numbers,
    validate_chunk_response,
)


def _decision(
    source_id: int,
    *,
    action: str = "keep",
    text: str | None = None,
    category: str = "educational",
) -> SegmentDecision:
    return SegmentDecision(
        source_segment_id=source_id,
        action=action,
        normalized_text=text,
        category=category,
        reason_code="test",
    )


def test_number_and_formula_extractors_cover_educational_tokens() -> None:
    text = "Задание № 12: x^2 + 3/4 ≥ √5, ответ 25%, формула C2H6."

    assert {"№12", "2", "3/4", "5", "25%"} <= set(extract_numbers(text))
    assert {"x", "^", "+", "/", "≥", "√", "%", "c", "2", "h", "6"} <= set(extract_formula_tokens(text))


def test_trim_that_adds_number_is_blocked() -> None:
    source = (SourceSegment(source_segment_id=1, text="Решаем x + 2 = 5."),)
    response = NormalizationChunkResponse(decisions=[_decision(1, action="trim", text="Решаем x + 2 = 6.")])

    with pytest.raises(UnsafeNormalizationResultError, match="новые числа"):
        validate_chunk_response(source, (1,), response, ValidationState())


def test_trim_cannot_add_or_paraphrase_regular_words() -> None:
    source = (SourceSegment(source_segment_id=1, text="Ну, сегодня решаем задачу."),)
    response = NormalizationChunkResponse(
        decisions=[
            _decision(
                1,
                action="trim",
                text="Сегодня быстро решаем задачу.",
            )
        ]
    )

    with pytest.raises(UnsafeNormalizationResultError, match="перефразировал"):
        validate_chunk_response(source, (1,), response, ValidationState())


def test_trim_that_removes_number_requires_manual_attention() -> None:
    source = (
        SourceSegment(
            source_segment_id=1,
            text="Ну, решаем задачу № 12.",
        ),
    )
    response = NormalizationChunkResponse(decisions=[_decision(1, action="trim", text="Решаем задачу.")])
    state = ValidationState()

    validate_chunk_response(source, (1,), response, state)

    assert state.numbers_preserved is False
    assert state.requires_manual_attention is True
    assert state.warnings == ["numbers_removed:1"]


def test_drop_with_formula_or_student_difficulty_is_blocked() -> None:
    formula = (SourceSegment(source_segment_id=1, speaker="П", text="x + 2 = 5"),)
    response = NormalizationChunkResponse(
        decisions=[_decision(1, action="drop", category="other_non_educational")]
    )
    with pytest.raises(UnsafeNormalizationResultError):
        validate_chunk_response(formula, (1,), response, ValidationState())

    difficulty = (
        SourceSegment(
            source_segment_id=2,
            speaker="У",
            text="Я не понимаю, почему знак меняется.",
        ),
    )
    response = NormalizationChunkResponse(
        decisions=[_decision(2, action="drop", category="other_non_educational")]
    )
    with pytest.raises(UnsafeNormalizationResultError):
        validate_chunk_response(difficulty, (2,), response, ValidationState())


def test_context_unknown_duplicate_and_missing_ids_are_rejected() -> None:
    source = (
        SourceSegment(source_segment_id=1, text="Цель", context_only=False),
        SourceSegment(source_segment_id=2, text="Контекст", context_only=True),
    )
    with pytest.raises(InvalidStructuredOutputError, match="контекстного"):
        validate_chunk_response(
            source,
            (1,),
            NormalizationChunkResponse(decisions=[_decision(2)]),
            ValidationState(),
        )
    with pytest.raises(InvalidStructuredOutputError, match="Неизвестный"):
        validate_chunk_response(
            source,
            (1,),
            NormalizationChunkResponse(decisions=[_decision(999)]),
            ValidationState(),
        )
    with pytest.raises(InvalidStructuredOutputError, match="Повторное"):
        validate_chunk_response(
            source,
            (1,),
            NormalizationChunkResponse(decisions=[_decision(1), _decision(1)]),
            ValidationState(),
        )
    with pytest.raises(IncompleteSegmentClassificationError, match="Пропущены"):
        validate_chunk_response(
            source,
            (1,),
            NormalizationChunkResponse(decisions=[]),
            ValidationState(),
        )


def test_keep_cannot_rewrite_source_text() -> None:
    source = (SourceSegment(source_segment_id=1, text="Ошибка ученика: 2 + 2 = 5"),)
    response = NormalizationChunkResponse(decisions=[_decision(1, text="Исправлено: 2 + 2 = 4")])

    with pytest.raises(InvalidStructuredOutputError, match="keep изменил"):
        validate_chunk_response(source, (1,), response, ValidationState())
