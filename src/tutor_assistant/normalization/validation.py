from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .errors import InvalidPlainTextOutputError, UnsafeNormalizationResultError
from .models import NormalizationQuality, SourceSegment
from .prompts import render_target_text
from .subjects import SubjectProfileName, get_subject_profile

NUMBER_PATTERN = re.compile(
    r"(?<![\w])(?:№\s*)?"
    r"(?:\d{1,4}[./-]\d{1,2}[./-]\d{1,4}"
    r"|\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)?"
    r"(?:\s*%|\s*[–—-]\s*\d+(?:[.,]\d+)?)?)"
    r"(?![\w])",
    flags=re.UNICODE,
)
FORMULA_PATTERN = re.compile(
    r"[+\-=<>≤≥/^√%()[\]{}→⇄]"
    r"|[A-Za-z]+"
    r"|(?<=[A-Za-zΑ-Ωα-ω])\d+"
    r"|\d+(?=[A-Za-zΑ-Ωα-ω])"
    r"|[Α-Ωα-ω]+"
    r"|[₀-₉₊₋₌₍₎]+"
    r"|[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾]+"
)
MATH_TERM_STEMS = get_subject_profile(SubjectProfileName.MATHEMATICS).all_protected_stems
PROTECTED_MARKERS = (
    "домашн",
    "дз",
    "к следующему занятию",
    "не понимаю",
    "не понял",
    "не поняла",
    "не получается",
    "сомнева",
    "ошиб",
    "почему",
)
NON_CONTENT_WORDS = {
    "это",
    "как",
    "что",
    "тогда",
    "здесь",
    "почему",
    "когда",
    "который",
    "которая",
    "которые",
    "меня",
    "тебя",
    "сейчас",
    "просто",
}


def extract_numbers(text: str) -> list[str]:
    return [
        re.sub(r"\s+", "", match.group(0)).replace(",", ".").casefold()
        for match in NUMBER_PATTERN.finditer(text)
    ]


def extract_formula_tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in FORMULA_PATTERN.finditer(text)]


def _normalized_lookup_text(text: str) -> str:
    normalized = text.casefold().replace("ё", "е").replace("²", "^2").replace("³", "^3")
    normalized = re.sub(r"\s*/\s*", "/", normalized)
    normalized = re.sub(r"\s*\^\s*", "^", normalized)
    return re.sub(r"\s+", " ", normalized)


def extract_subject_units(text: str, subject_profile: str) -> list[str]:
    profile = get_subject_profile(subject_profile)
    normalized = _normalized_lookup_text(text)
    found: list[str] = []
    for token in profile.unit_tokens:
        marker = _normalized_lookup_text(token)
        if len(marker) == 1 and marker.isalnum():
            pattern = re.compile(rf"(?<=\d)\s*{re.escape(marker)}(?![\w])")
        else:
            pattern = re.compile(rf"(?<![\w]){re.escape(marker)}(?![\w])")
        found.extend(marker for _ in pattern.finditer(normalized))
    return found


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+|[^\w\s]", text.casefold(), flags=re.UNICODE)


def _without_speaker_labels(text: str) -> str:
    return re.sub(r"(?m)^\[[^\]\n]+\]\s*", "", text)


def _is_token_subsequence(source: str, normalized: str) -> bool:
    iterator = iter(_tokens(source))
    return all(any(token == candidate for candidate in iterator) for token in _tokens(normalized))


def _counter_added(source: list[str], normalized: list[str]) -> list[str]:
    return list((Counter(normalized) - Counter(source)).elements())


def _counter_removed(source: list[str], normalized: list[str]) -> list[str]:
    return list((Counter(source) - Counter(normalized)).elements())


@dataclass(slots=True)
class ValidationState:
    numbers_preserved: bool = True
    formula_tokens_preserved: bool = True
    protected_content_preserved: bool = True
    subject_units_preserved: bool = True
    requires_manual_attention: bool = False
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: ValidationState) -> None:
        self.numbers_preserved &= other.numbers_preserved
        self.formula_tokens_preserved &= other.formula_tokens_preserved
        self.protected_content_preserved &= other.protected_content_preserved
        self.subject_units_preserved &= other.subject_units_preserved
        self.requires_manual_attention |= other.requires_manual_attention
        self.warnings.extend(other.warnings)

    def quality(self) -> NormalizationQuality:
        return NormalizationQuality(
            plain_text_valid=True,
            numbers_preserved=self.numbers_preserved,
            formula_tokens_preserved=self.formula_tokens_preserved,
            protected_content_preserved=self.protected_content_preserved,
            subject_units_preserved=self.subject_units_preserved,
            requires_manual_attention=self.requires_manual_attention,
            warnings=list(dict.fromkeys(self.warnings)),
        )


def _validate_protected_segments(
    target_segments: list[SourceSegment],
    normalized: str,
    state: ValidationState,
    *,
    subject_profile: str,
) -> None:
    profile = get_subject_profile(subject_profile)
    lowered = normalized.casefold().replace("ё", "е")
    normalized_words = set(re.findall(r"[а-яёa-z0-9]+", lowered))
    for segment in target_segments:
        source = segment.text.casefold().replace("ё", "е")
        matched_terms = [stem for stem in profile.all_protected_stems if stem in source]
        missing_terms = [stem for stem in matched_terms if stem not in lowered]
        if missing_terms:
            state.protected_content_preserved = False
            raise UnsafeNormalizationResultError(
                f"Удалён термин профиля {profile.name.value}: " + ", ".join(missing_terms)
            )

        protected_statement = any(marker in source for marker in PROTECTED_MARKERS)
        protected_statement |= segment.speaker == "У" and "?" in segment.text
        if not protected_statement:
            continue
        content_words = {
            word
            for word in re.findall(r"[а-яёa-z0-9]+", source)
            if len(word) >= 4 and word not in NON_CONTENT_WORDS
        }
        required = min(2, len(content_words))
        if required and len(content_words & normalized_words) < required:
            state.protected_content_preserved = False
            raise UnsafeNormalizationResultError(
                "Удалён защищённый вопрос, ошибка или учебное указание "
                f"source_segment_id={segment.source_segment_id}"
            )


def validate_plain_text_response(
    source_segments: tuple[SourceSegment, ...],
    target_ids: tuple[int, ...],
    response: str,
    state: ValidationState,
    *,
    subject_profile: str = SubjectProfileName.MATHEMATICS.value,
) -> str:
    profile = get_subject_profile(subject_profile)
    if not isinstance(response, str):
        raise InvalidPlainTextOutputError("Модель должна вернуть обычный текст")
    normalized = response.replace("\r\n", "\n").strip()
    if "\x00" in normalized:
        raise InvalidPlainTextOutputError("Ответ содержит недопустимый нулевой символ")
    first_line = normalized.splitlines()[0] if normalized else ""
    if normalized.startswith(("```", "{")) or (
        normalized.startswith("[") and not re.match(r"^\[[^\]\n]+\]\s+\S", first_line)
    ):
        raise InvalidPlainTextOutputError("Модель вернула JSON или служебную разметку вместо текста")

    source_by_id = {segment.source_segment_id: segment for segment in source_segments}
    unknown = set(target_ids) - source_by_id.keys()
    if unknown:
        raise InvalidPlainTextOutputError("Блок содержит неизвестные целевые сегменты")
    targets = [source_by_id[source_id] for source_id in target_ids]
    source_text = render_target_text(targets)
    semantic_source = _without_speaker_labels(source_text)
    semantic_normalized = _without_speaker_labels(normalized)

    source_numbers = extract_numbers(semantic_source)
    normalized_numbers = extract_numbers(semantic_normalized)
    if _counter_added(source_numbers, normalized_numbers):
        raise UnsafeNormalizationResultError("В нормализованном тексте появились новые числа")

    source_formula = extract_formula_tokens(semantic_source)
    normalized_formula = extract_formula_tokens(semantic_normalized)
    if _counter_added(source_formula, normalized_formula):
        raise UnsafeNormalizationResultError("В нормализованном тексте появились новые формульные токены")

    source_units = extract_subject_units(semantic_source, profile.name.value)
    normalized_units = extract_subject_units(semantic_normalized, profile.name.value)
    if _counter_added(source_units, normalized_units):
        raise UnsafeNormalizationResultError("В нормализованном тексте появились новые единицы измерения")

    for context in (segment for segment in source_segments if segment.context_only):
        context_text = context.text.strip()
        if context_text and context_text in normalized and context_text not in source_text:
            raise InvalidPlainTextOutputError(
                f"В ответ попал контекстный сегмент source_segment_id={context.source_segment_id}"
            )

    if normalized and not _is_token_subsequence(source_text, normalized):
        raise UnsafeNormalizationResultError("Модель добавила или перефразировала текст")
    if normalized and any(
        not re.match(r"^\[[^\]\n]+\]\s+\S", line) for line in normalized.splitlines() if line.strip()
    ):
        raise InvalidPlainTextOutputError("Каждая сохранённая реплика должна содержать метку говорящего")

    if _counter_removed(source_numbers, normalized_numbers):
        state.numbers_preserved = False
        state.requires_manual_attention = True
        state.warnings.append("numbers_removed")

    removed_formula = _counter_removed(source_formula, normalized_formula)
    if source_formula and not normalized_formula:
        raise UnsafeNormalizationResultError("Удалён формульный фрагмент целиком")
    if removed_formula:
        state.formula_tokens_preserved = False
        state.requires_manual_attention = True
        state.warnings.append("formula_tokens_removed")

    removed_units = _counter_removed(source_units, normalized_units)
    if removed_units:
        state.subject_units_preserved = False
        state.protected_content_preserved = False
        raise UnsafeNormalizationResultError(
            f"Удалены единицы профиля {profile.name.value}: " + ", ".join(removed_units)
        )

    _validate_protected_segments(
        targets,
        normalized,
        state,
        subject_profile=profile.name.value,
    )
    return normalized
