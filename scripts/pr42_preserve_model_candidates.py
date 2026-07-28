from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_range(path: str, start_marker: str, end_marker: str, replacement: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start) + len(end_marker)
    target.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


# Best-effort candidate extraction and quality propagation.
replace_once(
    "src/tutor_assistant/normalization/validation.py",
    "import re\n",
    "import json\nimport re\n",
)
replace_once(
    "src/tutor_assistant/normalization/validation.py",
    "}\n\n\ndef extract_numbers(text: str) -> list[str]:\n",
    "}" + r'''

REVIEW_CANDIDATE_KEYS = (
    "text",
    "output_text",
    "normalized_text",
    "educational_text",
    "content",
    "result",
    "response",
)


def _candidate_payload_text(payload: object) -> str | None:
    if isinstance(payload, str):
        return payload.strip() or None
    if isinstance(payload, dict):
        for key in REVIEW_CANDIDATE_KEYS:
            if key in payload:
                candidate = _candidate_payload_text(payload[key])
                if candidate:
                    return candidate
        for value in payload.values():
            candidate = _candidate_payload_text(value)
            if candidate:
                return candidate
        return None
    if isinstance(payload, list):
        parts = [
            candidate
            for item in payload
            if (candidate := _candidate_payload_text(item)) is not None
        ]
        return "\n".join(parts).strip() or None
    return None


def reviewable_candidate_text(response: object) -> str | None:
    """Return the most useful text the model produced, even when validation rejects it."""

    if not isinstance(response, str):
        return None
    text = (
        response.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\x00", "")
        .strip()
    )
    if not text:
        return None

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if not text:
            return None

    if text.startswith(("{", "[")):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            extracted = _candidate_payload_text(payload)
            if extracted:
                text = extracted
    return text.strip() or None


def extract_numbers(text: str) -> list[str]:
''',
)
replace_once(
    "src/tutor_assistant/normalization/validation.py",
    "class ValidationState:\n    numbers_preserved: bool = True\n",
    "class ValidationState:\n    plain_text_valid: bool = True\n    numbers_preserved: bool = True\n",
)
replace_once(
    "src/tutor_assistant/normalization/validation.py",
    "    def merge(self, other: ValidationState) -> None:\n        self.numbers_preserved &= other.numbers_preserved\n",
    "    def merge(self, other: ValidationState) -> None:\n        self.plain_text_valid &= other.plain_text_valid\n        self.numbers_preserved &= other.numbers_preserved\n",
)
replace_once(
    "src/tutor_assistant/normalization/validation.py",
    "        return cls(\n            numbers_preserved=quality.numbers_preserved,\n",
    "        return cls(\n            plain_text_valid=quality.plain_text_valid,\n            numbers_preserved=quality.numbers_preserved,\n",
)
replace_once(
    "src/tutor_assistant/normalization/validation.py",
    "            plain_text_valid=True,\n",
    "            plain_text_valid=self.plain_text_valid,\n",
)

# Public statistics remain backward-compatible through a default value.
replace_once(
    "src/tutor_assistant/normalization/models.py",
    "    source_fallback_chunks: int = Field(default=0, ge=0)\n",
    "    review_candidate_chunks: int = Field(default=0, ge=0)\n"
    "    source_fallback_chunks: int = Field(default=0, ge=0)\n",
)

# Preserve the last textual model response instead of replacing it with source text.
replace_once(
    "src/tutor_assistant/normalization/service.py",
    "from .validation import ValidationState, validate_plain_text_response\n",
    "from .validation import (\n"
    "    ValidationState,\n"
    "    reviewable_candidate_text,\n"
    "    validate_plain_text_response,\n"
    ")\n",
)
replace_once(
    "src/tutor_assistant/normalization/service.py",
    "                provider_requests,\n                source_fallback_chunks,\n            ) = self._normalize_chunks(\n",
    "                provider_requests,\n"
    "                review_candidate_chunks,\n"
    "                source_fallback_chunks,\n"
    "            ) = self._normalize_chunks(\n",
)
replace_once(
    "src/tutor_assistant/normalization/service.py",
    "                provider_requests=provider_requests,\n                source_fallback_chunks=source_fallback_chunks,\n",
    "                provider_requests=provider_requests,\n"
    "                review_candidate_chunks=review_candidate_chunks,\n"
    "                source_fallback_chunks=source_fallback_chunks,\n",
)
replace_once(
    "src/tutor_assistant/normalization/service.py",
    "    ) -> tuple[list[str], ValidationState, int, int, int, int]:\n",
    "    ) -> tuple[list[str], ValidationState, int, int, int, int, int]:\n",
)
replace_once(
    "src/tutor_assistant/normalization/service.py",
    "        provider_requests = 0\n        source_fallback_chunks = 0\n",
    "        provider_requests = 0\n"
    "        review_candidate_chunks = 0\n"
    "        source_fallback_chunks = 0\n",
)
replace_once(
    "src/tutor_assistant/normalization/service.py",
    "                state.merge(ValidationState.from_quality(checkpoint.quality))\n                reused_chunks += 1\n",
    "                state.merge(ValidationState.from_quality(checkpoint.quality))\n"
    "                checkpoint_warnings = checkpoint.quality.warnings\n"
    "                if any(item.startswith(\"model_candidate:\") for item in checkpoint_warnings):\n"
    "                    review_candidate_chunks += 1\n"
    "                if any(item.startswith(\"source_fallback:\") for item in checkpoint_warnings):\n"
    "                    source_fallback_chunks += 1\n"
    "                reused_chunks += 1\n",
)
old_start = "                    target_ids = set(chunk.target_ids)\n"
old_end = "                    break\n"
new_block = r'''                    raw_candidate = response.replace("\r\n", "\n").replace("\r", "\n").strip()
                    candidate = reviewable_candidate_text(response)
                    if candidate is not None:
                        error_message = str(exc)
                        lowered_error = error_message.casefold()
                        candidate_state = ValidationState(
                            plain_text_valid=(
                                not isinstance(exc, InvalidPlainTextOutputError)
                                or candidate != raw_candidate
                            ),
                            requires_manual_attention=True,
                            warnings=[
                                "model_candidate:"
                                f"chunk={chunk.index + 1}:"
                                f"error={type(exc).__name__}:"
                                f"message={error_message}"
                            ],
                        )
                        if "числ" in lowered_error:
                            candidate_state.numbers_preserved = False
                        if "формул" in lowered_error:
                            candidate_state.formula_tokens_preserved = False
                        if "единиц" in lowered_error:
                            candidate_state.subject_units_preserved = False
                            candidate_state.protected_content_preserved = False
                        if any(
                            marker in lowered_error
                            for marker in ("термин", "защищ", "контекст")
                        ):
                            candidate_state.protected_content_preserved = False
                        if run is not None:
                            self.checkpoints.complete(
                                run.id or 0,
                                chunk.index,
                                normalized_text=candidate,
                                quality=candidate_state.quality(),
                            )
                        normalized_chunks.append(candidate)
                        state.merge(candidate_state)
                        review_candidate_chunks += 1
                        completed_count += 1
                        logging.warning(
                            "event=content_filter_chunk_review_candidate lesson_id=%s "
                            "run_id=%s chunk_index=%d error_code=%s attempts=%d",
                            lesson_id,
                            run.id if run else "dry-run",
                            chunk.index,
                            type(exc).__name__,
                            attempt + 1,
                        )
                        self._emit_progress(
                            progress,
                            NormalizationProgress(
                                run_id=run.id if run else None,
                                current_chunk=chunk.index,
                                total_chunks=total_chunks,
                                completed_chunks=completed_count,
                                reused_chunks=reused_chunks,
                                provider_requests=provider_requests,
                                current_attempt=attempt + 1,
                                state="review_candidate",
                            ),
                        )
                        break

                    target_ids = set(chunk.target_ids)
                    fallback = render_target_text(
                        tuple(
                            item
                            for item in chunk.segments
                            if item.source_segment_id in target_ids
                        )
                    )
                    fallback_state = ValidationState(
                        requires_manual_attention=True,
                        warnings=[
                            "source_fallback:"
                            f"chunk={chunk.index + 1}:"
                            f"error={type(exc).__name__}:"
                            f"message={exc}"
                        ],
                    )
                    if run is not None:
                        self.checkpoints.complete(
                            run.id or 0,
                            chunk.index,
                            normalized_text=fallback,
                            quality=fallback_state.quality(),
                        )
                    normalized_chunks.append(fallback)
                    state.merge(fallback_state)
                    source_fallback_chunks += 1
                    completed_count += 1
                    logging.warning(
                        "event=content_filter_chunk_source_fallback lesson_id=%s "
                        "run_id=%s chunk_index=%d error_code=%s attempts=%d",
                        lesson_id,
                        run.id if run else "dry-run",
                        chunk.index,
                        type(exc).__name__,
                        attempt + 1,
                    )
                    self._emit_progress(
                        progress,
                        NormalizationProgress(
                            run_id=run.id if run else None,
                            current_chunk=chunk.index,
                            total_chunks=total_chunks,
                            completed_chunks=completed_count,
                            reused_chunks=reused_chunks,
                            provider_requests=provider_requests,
                            current_attempt=attempt + 1,
                            state="source_fallback",
                        ),
                    )
                    break
'''
service_path = ROOT / "src/tutor_assistant/normalization/service.py"
service_text = service_path.read_text(encoding="utf-8")
validation_except = service_text.index("                except RETRYABLE_VALIDATION_ERRORS as exc:\n")
block_start = service_text.index(old_start, validation_except)
block_end = service_text.index(old_end, block_start) + len(old_end)
service_path.write_text(
    service_text[:block_start] + new_block + service_text[block_end:],
    encoding="utf-8",
)
replace_once(
    "src/tutor_assistant/normalization/service.py",
    "            provider_requests,\n            source_fallback_chunks,\n        )\n",
    "            provider_requests,\n"
    "            review_candidate_chunks,\n"
    "            source_fallback_chunks,\n"
    "        )\n",
)
replace_once(
    "src/tutor_assistant/normalization/service.py",
    "        provider_requests: int,\n        source_fallback_chunks: int,\n",
    "        provider_requests: int,\n"
    "        review_candidate_chunks: int,\n"
    "        source_fallback_chunks: int,\n",
)
replace_once(
    "src/tutor_assistant/normalization/service.py",
    "                provider_requests=provider_requests,\n                source_fallback_chunks=source_fallback_chunks,\n",
    "                provider_requests=provider_requests,\n"
    "                review_candidate_chunks=review_candidate_chunks,\n"
    "                source_fallback_chunks=source_fallback_chunks,\n",
)

# Surface the distinction in the state-driven GUI.
replace_once(
    "src/tutor_assistant/ui/app.py",
    "            f\"Сохранено {statistics.retained_ratio * 100:.1f}% текста · \"\n"
    "            f\"fallback-блоков: {statistics.source_fallback_chunks} · \"\n"
    "            f\"запросов к модели: {statistics.provider_requests}\"\n",
    "            f\"Сохранено {statistics.retained_ratio * 100:.1f}% текста · \"\n"
    "            f\"кандидатов на проверку: {statistics.review_candidate_chunks} · \"\n"
    "            f\"fallback-блоков: {statistics.source_fallback_chunks} · \"\n"
    "            f\"запросов к модели: {statistics.provider_requests}\"\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "            fallback_chunks = (\n"
    "                preview.statistics.source_fallback_chunks if preview else 0\n"
    "            )\n"
    "            warnings = len(preview.quality.warnings) if preview else 0\n",
    "            review_candidates = (\n"
    "                preview.statistics.review_candidate_chunks if preview else 0\n"
    "            )\n"
    "            fallback_chunks = (\n"
    "                preview.statistics.source_fallback_chunks if preview else 0\n"
    "            )\n"
    "            warnings = len(preview.quality.warnings) if preview else 0\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "                    f\"Fallback-блоков: {fallback_chunks} · предупреждений: {warnings}. \"\n"
    "                    \"Результат не будет применён без вашего подтверждения.\"\n"
    "                ),\n"
    "                tone=\"warning\" if fallback_chunks or warnings else \"success\",\n",
    "                    f\"Кандидатов модели: {review_candidates} · \"\n"
    "                    f\"fallback-блоков: {fallback_chunks} · предупреждений: {warnings}. \"\n"
    "                    \"Результат не будет применён без вашего подтверждения.\"\n"
    "                ),\n"
    "                tone=(\n"
    "                    \"warning\"\n"
    "                    if review_candidates or fallback_chunks or warnings\n"
    "                    else \"success\"\n"
    "                ),\n",
)

# Tests: candidate extraction, preservation, and source-only fallback when no text exists.
replace_once(
    "tests/test_normalization_validation.py",
    "    extract_numbers,\n    validate_plain_text_response,\n",
    "    extract_numbers,\n    reviewable_candidate_text,\n    validate_plain_text_response,\n",
)
replace_once(
    "tests/test_normalization_validation.py",
    "\n\ndef test_number_and_formula_extractors_cover_educational_tokens() -> None:\n",
    r'''

def test_reviewable_candidate_extracts_json_and_markdown_text() -> None:
    assert reviewable_candidate_text(
        '{"text":"[П] Решаем x + 2 = 6."}'
    ) == "[П] Решаем x + 2 = 6."
    assert reviewable_candidate_text(
        "```text\n[П] Решаем x + 2 = 6.\n```"
    ) == "[П] Решаем x + 2 = 6."
    assert reviewable_candidate_text("") is None


def test_number_and_formula_extractors_cover_educational_tokens() -> None:
''',
)
replace_range(
    "tests/test_normalization_service.py",
    "def test_rejected_responses_use_source_fallback_and_finish_review(tmp_path: Path) -> None:\n",
    "\n\ndef test_apply_creates_revision_and_marks_run_approved",
    r'''def test_rejected_responses_preserve_last_model_candidate_for_review(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(
        responses=[
            '{"text":"wrong contract"}',
            (
                '{"text":"[П] Сегодня решаем неравенство x + 2 > 6.\\n'
                '[У] Я не понимаю, почему знак меняется."}'
            ),
        ]
    )
    service, _content, lesson, source_path = _setup(
        tmp_path,
        provider,
        config=NormalizationConfig(retry_requests=1, retry_backoff_seconds=0),
    )
    source_before = source_path.read_bytes()

    result = service.normalize_lesson(lesson.lesson_id)

    assert result.run and result.run.status == NormalizationRunStatus.REVIEW_REQUIRED
    assert result.transcript.statistics.review_candidate_chunks == 1
    assert result.transcript.statistics.source_fallback_chunks == 0
    assert result.transcript.quality.requires_manual_attention is True
    assert any("model_candidate:" in item for item in result.transcript.quality.warnings)
    assert "x + 2 > 6" in result.transcript.educational_text
    assert source_path.read_bytes() == source_before


def test_model_number_change_is_preserved_in_review_candidate(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(
        default=(
            "[П] Сегодня решаем неравенство x + 2 > 6.\n"
            "[У] Я не понимаю, почему знак меняется."
        )
    )
    service, _content, lesson, _source_path = _setup(tmp_path, provider)

    result = service.normalize_lesson(lesson.lesson_id)

    assert "x + 2 > 6" in result.transcript.educational_text
    assert result.transcript.statistics.review_candidate_chunks == 1
    assert result.transcript.statistics.source_fallback_chunks == 0
    assert result.transcript.quality.numbers_preserved is False
    assert result.transcript.quality.requires_manual_attention is True


def test_empty_rejected_response_uses_source_fallback(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(default="")
    service, _content, lesson, _source_path = _setup(tmp_path, provider)

    result = service.normalize_lesson(lesson.lesson_id)

    assert result.transcript.statistics.review_candidate_chunks == 0
    assert result.transcript.statistics.source_fallback_chunks == 1
    assert "x + 2 > 5" in result.transcript.educational_text
    assert any("source_fallback:" in item for item in result.transcript.quality.warnings)


def test_apply_creates_revision_and_marks_run_approved''',
)

# User-facing documentation and version.
replace_once("pyproject.toml", 'version = "0.17.0"\n', 'version = "0.18.0"\n')
replace_once(
    "src/tutor_assistant/__init__.py",
    '__version__ = "0.17.0"\n',
    '__version__ = "0.18.0"\n',
)
replace_once(
    "README.md",
    "Текущая версия: **0.17.0**.\n",
    "Текущая версия: **0.18.0**.\n",
)
replace_once(
    "README.md",
    "Числа, формулы, вопросы и ошибки ученика, домашнее задание и термины школьной\n"
    "математики защищаются детерминированной проверкой. Результат всегда требует\n"
    "ручного применения. Архитектура и настройка описаны в\n",
    "Числа, формулы, вопросы и ошибки ученика, домашнее задание и термины школьной\n"
    "математики проверяются детерминированно. Если модель всё же добавила число,\n"
    "перефразировала текст или изменила формулу, её ответ не отбрасывается: он\n"
    "сохраняется как кандидат и обязательно передаётся преподавателю на ручную\n"
    "проверку. Исходный блок используется только когда модель не вернула текста.\n"
    "Результат всегда требует ручного применения. Архитектура и настройка описаны в\n",
)
