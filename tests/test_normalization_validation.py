from __future__ import annotations

import pytest

from tutor_assistant.normalization.errors import (
    InvalidPlainTextOutputError,
    UnsafeNormalizationResultError,
)
from tutor_assistant.normalization.models import SourceSegment
from tutor_assistant.normalization.validation import (
    ValidationState,
    extract_formula_tokens,
    extract_numbers,
    validate_plain_text_response,
)


def test_number_and_formula_extractors_cover_educational_tokens() -> None:
    text = "Задание № 12: x^2 + 3/4 ≥ √5, ответ 25%, формула C2H6."

    assert {"№12", "2", "3/4", "5", "25%"} <= set(extract_numbers(text))
    assert {"x", "^", "+", "/", "≥", "√", "%", "c", "2", "h", "6"} <= set(extract_formula_tokens(text))


def test_plain_text_that_adds_number_is_blocked() -> None:
    source = (SourceSegment(source_segment_id=1, speaker="П", text="Решаем x + 2 = 5."),)

    with pytest.raises(UnsafeNormalizationResultError, match="новые числа"):
        validate_plain_text_response(
            source,
            (1,),
            "[П] Решаем x + 2 = 6.",
            ValidationState(),
        )


def test_plain_text_cannot_add_or_paraphrase_regular_words() -> None:
    source = (SourceSegment(source_segment_id=1, speaker="П", text="Ну, сегодня решаем задачу."),)

    with pytest.raises(UnsafeNormalizationResultError, match="перефразировала"):
        validate_plain_text_response(
            source,
            (1,),
            "[П] Сегодня быстро решаем задачу.",
            ValidationState(),
        )


def test_removed_number_requires_manual_attention() -> None:
    source = (
        SourceSegment(
            source_segment_id=1,
            speaker="П",
            text="Ну, решаем задачу № 12.",
        ),
    )
    state = ValidationState()

    result = validate_plain_text_response(
        source,
        (1,),
        "[П] Решаем задачу.",
        state,
    )

    assert result == "[П] Решаем задачу."
    assert state.numbers_preserved is False
    assert state.requires_manual_attention is True
    assert state.warnings == ["numbers_removed"]


def test_formula_and_school_math_terms_cannot_be_dropped_wholly() -> None:
    formula = (SourceSegment(source_segment_id=1, speaker="П", text="x + 2 = 5"),)
    with pytest.raises(UnsafeNormalizationResultError, match="формульный"):
        validate_plain_text_response(formula, (1,), "", ValidationState())

    logarithm = (
        SourceSegment(
            source_segment_id=2,
            speaker="П",
            text="Рассмотрим свойства логарифмов.",
        ),
    )
    with pytest.raises(UnsafeNormalizationResultError, match="термин школьного курса"):
        validate_plain_text_response(logarithm, (2,), "", ValidationState())


def test_student_question_and_difficulty_are_protected() -> None:
    source = (
        SourceSegment(
            source_segment_id=1,
            speaker="У",
            text="Я не понимаю, почему знак меняется.",
        ),
    )

    with pytest.raises(UnsafeNormalizationResultError, match="защищённый"):
        validate_plain_text_response(source, (1,), "", ValidationState())


def test_context_text_and_json_are_rejected() -> None:
    source = (
        SourceSegment(source_segment_id=1, speaker="П", text="Целевой текст."),
        SourceSegment(
            source_segment_id=2,
            speaker="П",
            text="Только контекст.",
            context_only=True,
        ),
    )

    with pytest.raises(InvalidPlainTextOutputError, match="контекстный"):
        validate_plain_text_response(
            source,
            (1,),
            "[П] Только контекст.",
            ValidationState(),
        )
    with pytest.raises(InvalidPlainTextOutputError, match="JSON"):
        validate_plain_text_response(
            source,
            (1,),
            '{"text":"Целевой текст"}',
            ValidationState(),
        )


def test_greeting_may_be_removed_to_empty_text() -> None:
    source = (SourceSegment(source_segment_id=1, speaker="П", text="Здравствуйте, меня слышно?"),)

    assert validate_plain_text_response(source, (1,), "", ValidationState()) == ""


def test_each_retained_line_requires_speaker_label() -> None:
    source = (SourceSegment(source_segment_id=1, speaker="П", text="Решаем задачу."),)

    with pytest.raises(InvalidPlainTextOutputError, match="метку говорящего"):
        validate_plain_text_response(source, (1,), "Решаем задачу.", ValidationState())


def test_plain_text_supports_custom_and_missing_source_speakers() -> None:
    source = (
        SourceSegment(source_segment_id=1, speaker="Teacher", text="Решаем задачу."),
        SourceSegment(source_segment_id=2, text="Ответ равен пяти."),
    )

    assert (
        validate_plain_text_response(
            source,
            (1, 2),
            "[Teacher] Решаем задачу.\n[—] Ответ равен пяти.",
            ValidationState(),
        )
        == "[Teacher] Решаем задачу.\n[—] Ответ равен пяти."
    )
