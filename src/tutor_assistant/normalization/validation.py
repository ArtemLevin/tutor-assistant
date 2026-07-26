from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .errors import (
    IncompleteSegmentClassificationError,
    InvalidStructuredOutputError,
    UnsafeNormalizationResultError,
)
from .models import (
    NormalizationChunkResponse,
    NormalizationQuality,
    SegmentDecision,
    SourceSegment,
)

NUMBER_PATTERN = re.compile(
    r"(?<![\w])(?:№\s*)?"
    r"(?:\d{1,4}[./-]\d{1,2}[./-]\d{1,4}"
    r"|\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)?"
    r"(?:\s*%|\s*[–—-]\s*\d+(?:[.,]\d+)?)?)"
    r"(?![\w])",
    flags=re.UNICODE,
)
FORMULA_PATTERN = re.compile(
    r"[+\-=<>≤≥/^√%()[\]]"
    r"|[A-Za-z]+"
    r"|(?<=[A-Za-zΑ-Ωα-ω])\d+"
    r"|\d+(?=[A-Za-zΑ-Ωα-ω])"
    r"|[Α-Ωα-ω]+"
    r"|[₀-₉₊₋₌₍₎]+"
    r"|[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾]+"
)


def extract_numbers(text: str) -> list[str]:
    return [
        re.sub(r"\s+", "", match.group(0)).replace(",", ".").casefold()
        for match in NUMBER_PATTERN.finditer(text)
    ]


def extract_formula_tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in FORMULA_PATTERN.finditer(text)]


@dataclass(slots=True)
class ValidationState:
    numbers_preserved: bool = True
    formula_tokens_preserved: bool = True
    requires_manual_attention: bool = False
    warnings: list[str] = field(default_factory=list)

    def quality(self) -> NormalizationQuality:
        return NormalizationQuality(
            schema_valid=True,
            all_source_segments_classified=True,
            numbers_preserved=self.numbers_preserved,
            formula_tokens_preserved=self.formula_tokens_preserved,
            requires_manual_attention=self.requires_manual_attention,
            warnings=list(dict.fromkeys(self.warnings)),
        )


def _counter_added(source: list[str], normalized: list[str]) -> list[str]:
    return list((Counter(normalized) - Counter(source)).elements())


def _counter_removed(source: list[str], normalized: list[str]) -> list[str]:
    return list((Counter(source) - Counter(normalized)).elements())


def _is_token_subsequence(source: str, normalized: str) -> bool:
    source_tokens = re.findall(r"\w+|[^\w\s]", source.casefold(), flags=re.UNICODE)
    normalized_tokens = re.findall(
        r"\w+|[^\w\s]",
        normalized.casefold(),
        flags=re.UNICODE,
    )
    iterator = iter(source_tokens)
    return all(any(token == candidate for candidate in iterator) for token in normalized_tokens)


def validate_chunk_response(
    source_segments: tuple[SourceSegment, ...],
    target_ids: tuple[int, ...],
    response: NormalizationChunkResponse,
    state: ValidationState,
) -> dict[int, SegmentDecision]:
    source = {segment.source_segment_id: segment for segment in source_segments}
    target_set = set(target_ids)
    decisions: dict[int, SegmentDecision] = {}
    for decision in response.decisions:
        source_id = decision.source_segment_id
        if source_id not in source:
            raise InvalidStructuredOutputError(f"Неизвестный source_segment_id={source_id}")
        if source[source_id].context_only or source_id not in target_set:
            raise InvalidStructuredOutputError(
                f"Решение для контекстного сегмента source_segment_id={source_id}"
            )
        if source_id in decisions:
            raise InvalidStructuredOutputError(f"Повторное решение для source_segment_id={source_id}")
        decisions[source_id] = decision

    missing = target_set - decisions.keys()
    if missing:
        ids = ", ".join(str(item) for item in sorted(missing))
        raise IncompleteSegmentClassificationError(f"Пропущены сегменты: {ids}")

    for source_id in target_ids:
        segment = source[source_id]
        decision = decisions[source_id]
        if decision.action == "keep":
            if decision.normalized_text not in {None, segment.text}:
                raise InvalidStructuredOutputError(f"keep изменил текст source_segment_id={source_id}")
            if not segment.text.strip():
                raise InvalidStructuredOutputError(f"keep сохранил пустой source_segment_id={source_id}")
            continue
        if decision.action == "drop":
            lowered = segment.text.casefold()
            explicitly_non_educational = decision.category in {
                "greeting",
                "farewell",
                "audio_check",
                "video_check",
                "screen_sharing",
                "technical_issue",
                "small_talk",
                "filler",
                "duplicate",
                "background_noise",
                "other_non_educational",
            }
            protected_language = (
                "?" in segment.text
                and not explicitly_non_educational
                or segment.speaker == "У"
                and any(
                    marker in lowered
                    for marker in (
                        "не понимаю",
                        "не понял",
                        "не поняла",
                        "не получается",
                        "сомнева",
                        "ошиб",
                        "почему",
                    )
                )
                or any(
                    marker in lowered
                    for marker in (
                        "домашн",
                        "дз",
                        "задание №",
                        "задача №",
                        "к следующему занятию",
                    )
                )
            )
            if (
                decision.category == "educational"
                or extract_numbers(segment.text)
                or extract_formula_tokens(segment.text)
                or protected_language
            ):
                raise UnsafeNormalizationResultError(
                    f"drop затронул защищённый учебный фрагмент source_segment_id={source_id}"
                )
            continue
        normalized = (decision.normalized_text or "").strip()
        source_numbers = extract_numbers(segment.text)
        normalized_numbers = extract_numbers(normalized)
        added_numbers = _counter_added(source_numbers, normalized_numbers)
        if added_numbers:
            raise UnsafeNormalizationResultError(
                f"В trim появились новые числа source_segment_id={source_id}"
            )
        if _counter_removed(source_numbers, normalized_numbers):
            state.numbers_preserved = False
            state.requires_manual_attention = True
            state.warnings.append(f"numbers_removed:{source_id}")

        source_tokens = extract_formula_tokens(segment.text)
        normalized_tokens = extract_formula_tokens(normalized)
        added_tokens = _counter_added(source_tokens, normalized_tokens)
        if added_tokens:
            raise UnsafeNormalizationResultError(
                f"В trim появились новые формульные токены source_segment_id={source_id}"
            )
        if not _is_token_subsequence(segment.text, normalized):
            raise UnsafeNormalizationResultError(
                f"trim добавил или перефразировал текст source_segment_id={source_id}"
            )
        if _counter_removed(source_tokens, normalized_tokens):
            state.formula_tokens_preserved = False
            state.requires_manual_attention = True
            state.warnings.append(f"formula_tokens_removed:{source_id}")
    return decisions
