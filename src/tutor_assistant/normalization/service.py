from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter, sleep
from typing import Any

from ..config import NormalizationConfig
from ..content import ContentNotFoundError, StudentContentService
from ..domain import JobStatus
from .artifacts import (
    NormalizationRunStore,
    configuration_hash,
    source_sha256,
    write_json_atomic,
)
from .chunking import chunk_segments, sort_segments
from .errors import (
    IncompleteSegmentClassificationError,
    InvalidStructuredOutputError,
    NormalizationCancelledError,
    NormalizationError,
    OllamaTimeoutError,
    SourceTranscriptChangedError,
    UnsafeNormalizationResultError,
)
from .models import (
    NormalizationChunkRequest,
    NormalizationExecution,
    NormalizationManifest,
    NormalizationQuality,
    NormalizationRun,
    NormalizationRunStatus,
    NormalizationStatistics,
    NormalizedSegment,
    NormalizedTranscript,
    RemovedFragment,
    SegmentDecision,
    SourceSegment,
)
from .ollama_client import OllamaClient
from .prompts import PROMPT_VERSION
from .protocol import CancellationToken, NormalizationProvider
from .validation import ValidationState, validate_chunk_response

ProviderFactory = Callable[[NormalizationConfig, str], NormalizationProvider]
RETRYABLE_OUTPUT_ERRORS = (
    InvalidStructuredOutputError,
    IncompleteSegmentClassificationError,
    UnsafeNormalizationResultError,
    OllamaTimeoutError,
)


def _default_provider(config: NormalizationConfig, model: str) -> NormalizationProvider:
    return OllamaClient(config, model=model)


class NormalizationService:
    def __init__(
        self,
        config: NormalizationConfig,
        content_service: StudentContentService,
        *,
        provider_factory: ProviderFactory = _default_provider,
    ) -> None:
        self.config = config
        self.content_service = content_service
        self.provider_factory = provider_factory
        self.runs = NormalizationRunStore(content_service.repository)

    def recover_interrupted(self) -> int:
        if self.content_service.active_activities():
            return 0
        with self.content_service.activity(
            "transcript-normalization-recovery",
            exclusive=True,
        ):
            return self.runs.recover_interrupted()

    @staticmethod
    def load_source_segments(path: Path) -> list[SourceSegment]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise NormalizationError(f"Не удалось прочитать исходные сегменты: {path}") from exc
        if not isinstance(payload, list):
            raise NormalizationError("Файл исходных сегментов должен содержать JSON-массив")
        segments = []
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                raise NormalizationError(f"Некорректный сегмент #{index}")
            source_id = item.get("source_segment_id", item.get("id", index))
            segments.append(
                SourceSegment(
                    source_segment_id=int(source_id),
                    start=item.get("start"),
                    end=item.get("end"),
                    speaker=item.get("speaker"),
                    text=str(item.get("text") or ""),
                )
            )
        return sort_segments(segments)

    def lesson_source_segments(self, lesson_id: str) -> tuple[list[SourceSegment], str]:
        content = self.content_service.get_lesson(lesson_id)
        path_value = content.lesson.artifacts.segments_json
        if not path_value:
            raise NormalizationError("У занятия отсутствуют исходные сегменты транскрипта")
        path = Path(path_value)
        if not path.is_file():
            raise NormalizationError(f"Файл исходных сегментов не найден: {path}")
        return self.load_source_segments(path), path.name

    def _configuration_payload(self, model: str, include_removed_text: bool) -> dict[str, Any]:
        payload = self.config.model_dump(mode="json")
        payload["model"] = model
        payload["include_removed_text"] = include_removed_text
        return payload

    def normalize_lesson(
        self,
        lesson_id: str,
        *,
        model: str | None = None,
        force: bool = False,
        dry_run: bool = False,
        output: Path | None = None,
        include_removed_text: bool | None = None,
        source_segments: list[SourceSegment] | None = None,
        source_artifact: str | None = None,
        cancellation: CancellationToken | None = None,
    ) -> NormalizationExecution:
        if not self.config.enabled and not dry_run:
            raise NormalizationError("Локальная нормализация отключена в конфигурации")
        selected_model = model or self.config.model
        include_removed = (
            self.config.include_removed_text if include_removed_text is None else include_removed_text
        )
        cancellation = cancellation or CancellationToken()
        with self.content_service.activity(
            "transcript-normalization",
            lesson_id=lesson_id,
            exclusive=True,
            ttl=timedelta(minutes=2),
        ):
            return self._normalize_locked(
                lesson_id,
                model=selected_model,
                force=force,
                dry_run=dry_run,
                output=output,
                include_removed_text=include_removed,
                source_segments=source_segments,
                source_artifact=source_artifact,
                cancellation=cancellation,
            )

    def _normalize_locked(
        self,
        lesson_id: str,
        *,
        model: str,
        force: bool,
        dry_run: bool,
        output: Path | None,
        include_removed_text: bool,
        source_segments: list[SourceSegment] | None,
        source_artifact: str | None,
        cancellation: CancellationToken,
    ) -> NormalizationExecution:
        lesson = self.content_service.repository.get_lesson(lesson_id)
        if lesson is None:
            raise ContentNotFoundError(f"Занятие не найдено: {lesson_id}")
        if source_segments is None:
            source_segments, loaded_artifact = self.lesson_source_segments(lesson_id)
            source_artifact = source_artifact or loaded_artifact
        else:
            source_segments = sort_segments(source_segments)
            source_artifact = source_artifact or "review-buffer"
        if not source_segments:
            raise NormalizationError("Транскрипт не содержит сегментов")
        source_ids = [segment.source_segment_id for segment in source_segments]
        if len(source_ids) != len(set(source_ids)):
            raise NormalizationError("Исходный транскрипт содержит повторяющиеся ID сегментов")

        source_hash = source_sha256(source_segments)
        config_hash = configuration_hash(self._configuration_payload(model, include_removed_text))
        run: NormalizationRun | None = None
        if not dry_run:
            run, created = self.runs.create_or_get(
                lesson_id=lesson_id,
                source_hash=source_hash,
                model=model,
                prompt_version=PROMPT_VERSION,
                config_hash=config_hash,
                force=force,
            )
            if (
                not created
                and run.status
                in {
                    NormalizationRunStatus.REVIEW_REQUIRED,
                    NormalizationRunStatus.APPROVED,
                }
                and run.artifact_path
            ):
                artifact = self.content_service.workspace / run.artifact_path
                if artifact.is_file():
                    transcript = NormalizedTranscript.model_validate_json(
                        artifact.read_text(encoding="utf-8")
                    )
                    return NormalizationExecution(
                        run=run,
                        transcript=transcript,
                        artifact_path=str(artifact),
                        manifest_path=lesson.artifacts.normalization_manifest,
                        reused=True,
                    )
            run = self.runs.mark_running(run.id or 0)

        provider = self.provider_factory(self.config, model)
        started_at = datetime.now(UTC)
        started = perf_counter()
        try:
            cancellation.raise_if_cancelled()
            provider.check_available(model)
            chunks = chunk_segments(
                source_segments,
                max_segments=self.config.max_segments_per_chunk,
                max_characters=self.config.max_input_characters,
                overlap_segments=self.config.context_overlap_segments,
            )
            decisions, validation, attempts = self._classify_chunks(
                lesson_id,
                chunks,
                provider,
                run,
                cancellation,
            )
            transcript = self._build_transcript(
                lesson_id=lesson_id,
                source_segments=source_segments,
                decisions=decisions,
                source_artifact=source_artifact,
                source_hash=source_hash,
                model=model,
                validation=validation,
                include_removed_text=include_removed_text,
                created_at=datetime.now(UTC),
            )
            cancellation.raise_if_cancelled()
            if output is None:
                if dry_run:
                    directory = Path(tempfile.mkdtemp(prefix="tutor-normalization-"))
                    artifact_path = directory / "transcript_normalized.json"
                else:
                    artifact_path = (
                        self.content_service.workspace
                        / "lessons"
                        / lesson_id
                        / "transcript"
                        / "transcript_normalized.json"
                    )
            else:
                artifact_path = output.resolve()
            write_json_atomic(artifact_path, transcript)

            manifest_path: Path | None = None
            if not dry_run and run is not None:
                completed_at = datetime.now(UTC)
                persisted_run = self.runs.get(run.id or 0)
                manifest_path = artifact_path.with_name("normalization_manifest.json")
                manifest = NormalizationManifest(
                    provider=self.config.provider,
                    model=model,
                    prompt_version=PROMPT_VERSION,
                    source_sha256=source_hash,
                    configuration_hash=config_hash,
                    started_at=started_at,
                    completed_at=completed_at,
                    elapsed_seconds=round(perf_counter() - started, 3),
                    chunk_count=len(chunks),
                    attempts=persisted_run.attempts if persisted_run else attempts,
                    status=NormalizationRunStatus.REVIEW_REQUIRED,
                )
                write_json_atomic(manifest_path, manifest.model_dump(mode="json"))
                try:
                    relative_artifact = artifact_path.relative_to(self.content_service.workspace).as_posix()
                except ValueError:
                    relative_artifact = str(artifact_path.resolve())
                run = self.runs.finish(
                    run.id or 0,
                    NormalizationRunStatus.REVIEW_REQUIRED,
                    artifact_path=relative_artifact,
                )
                current = self.content_service.get_lesson(lesson_id).lesson
                current.artifacts.normalized_transcript_json = str(artifact_path.resolve())
                current.artifacts.normalization_manifest = str(manifest_path.resolve())
                self.content_service.persist_pipeline_lesson(
                    current,
                    frozenset({"artifacts"}),
                )
            logging.info(
                "event=normalization_completed lesson_id=%s model=%s chunks=%d "
                "segments=%d elapsed_seconds=%.3f retained_ratio=%.3f",
                lesson_id,
                model,
                len(chunks),
                len(source_segments),
                perf_counter() - started,
                transcript.statistics.retained_ratio,
            )
            return NormalizationExecution(
                run=run,
                transcript=transcript,
                artifact_path=str(artifact_path),
                manifest_path=str(manifest_path) if manifest_path else None,
            )
        except NormalizationCancelledError as exc:
            if run is not None:
                self.runs.finish(
                    run.id or 0,
                    NormalizationRunStatus.CANCELLED,
                    error=str(exc),
                )
            raise
        except Exception as exc:
            if run is not None:
                self.runs.finish(
                    run.id or 0,
                    NormalizationRunStatus.FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                )
            logging.exception(
                "event=normalization_failed lesson_id=%s model=%s error_code=%s",
                lesson_id,
                model,
                type(exc).__name__,
            )
            raise

    def _classify_chunks(
        self,
        lesson_id: str,
        chunks,
        provider: NormalizationProvider,
        run: NormalizationRun | None,
        cancellation: CancellationToken,
    ) -> tuple[dict[int, SegmentDecision], ValidationState, int]:
        all_decisions: dict[int, SegmentDecision] = {}
        state = ValidationState()
        attempts_total = 0
        for chunk in chunks:
            request = NormalizationChunkRequest(
                lesson_id=lesson_id,
                prompt_version=PROMPT_VERSION,
                mode=self.config.mode,
                segments=list(chunk.segments),
            )
            validation_errors: tuple[str, ...] = ()
            for attempt in range(self.config.max_attempts):
                cancellation.raise_if_cancelled()
                attempts_total += 1
                if run is not None:
                    self.runs.increment_attempts(run.id or 0)
                try:
                    response = provider.normalize_chunk(
                        request,
                        validation_errors=validation_errors,
                        cancellation=cancellation,
                    )
                    candidate_state = ValidationState()
                    decisions = validate_chunk_response(
                        chunk.segments,
                        chunk.target_ids,
                        response,
                        candidate_state,
                    )
                    duplicate = set(all_decisions) & set(decisions)
                    if duplicate:
                        raise InvalidStructuredOutputError(
                            "Повторные решения между блоками: "
                            + ", ".join(str(item) for item in sorted(duplicate))
                        )
                    all_decisions.update(decisions)
                    state.numbers_preserved &= candidate_state.numbers_preserved
                    state.formula_tokens_preserved &= candidate_state.formula_tokens_preserved
                    state.requires_manual_attention |= candidate_state.requires_manual_attention
                    state.warnings.extend(candidate_state.warnings)
                    break
                except RETRYABLE_OUTPUT_ERRORS as exc:
                    if attempt + 1 >= self.config.max_attempts:
                        raise
                    validation_errors = (f"{type(exc).__name__}: {exc}",)
                    if self.config.retry_backoff_seconds:
                        sleep(self.config.retry_backoff_seconds)
            else:
                raise InvalidStructuredOutputError("Не удалось проверить ответ блока")
        return all_decisions, state, attempts_total

    def _build_transcript(
        self,
        *,
        lesson_id: str,
        source_segments: list[SourceSegment],
        decisions: dict[int, SegmentDecision],
        source_artifact: str,
        source_hash: str,
        model: str,
        validation: ValidationState,
        include_removed_text: bool,
        created_at: datetime,
    ) -> NormalizedTranscript:
        normalized_segments: list[NormalizedSegment] = []
        removed: list[RemovedFragment] = []
        educational_lines: list[str] = []
        kept = trimmed = dropped = 0
        normalized_characters = 0

        for source in source_segments:
            decision = decisions.get(source.source_segment_id)
            if decision is None:
                raise IncompleteSegmentClassificationError(
                    f"Нет решения для source_segment_id={source.source_segment_id}"
                )
            if decision.action == "drop":
                dropped += 1
                removed.append(
                    RemovedFragment(
                        source_segment_ids=[source.source_segment_id],
                        category=decision.category,
                        reason_code=decision.reason_code,
                        text=source.text if include_removed_text else None,
                    )
                )
                continue
            if decision.action == "trim":
                trimmed += 1
                text = (decision.normalized_text or "").strip()
            else:
                kept += 1
                text = source.text
            normalized_characters += len(text)
            normalized = NormalizedSegment(
                id=f"normalized-{len(normalized_segments) + 1:04d}",
                source_segment_ids=[source.source_segment_id],
                speaker=source.speaker,
                start=source.start,
                end=source.end,
                content_type=decision.category,
                text=text,
            )
            normalized_segments.append(normalized)
            educational_lines.append(f"[{source.speaker}] {text}" if source.speaker else text)

        source_characters = sum(len(segment.text) for segment in source_segments)
        retained_ratio = normalized_characters / source_characters if source_characters else 1.0
        if 1 - retained_ratio > self.config.high_removal_threshold:
            validation.requires_manual_attention = True
            validation.warnings.append("unusually_high_removal_ratio")
        quality: NormalizationQuality = validation.quality()
        return NormalizedTranscript(
            lesson_id=lesson_id,
            source={
                "artifact": source_artifact,
                "sha256": source_hash,
                "segment_count": len(source_segments),
                "language": "ru",
            },
            normalizer={
                "provider": self.config.provider,
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "mode": self.config.mode,
                "created_at": created_at.isoformat(),
            },
            educational_text="\n".join(educational_lines),
            segments=normalized_segments,
            removed_fragments=removed,
            statistics=NormalizationStatistics(
                source_characters=source_characters,
                normalized_characters=normalized_characters,
                retained_ratio=round(retained_ratio, 6),
                source_segments=len(source_segments),
                kept_segments=kept,
                trimmed_segments=trimmed,
                removed_segments=dropped,
            ),
            quality=quality,
        )

    def apply_result(
        self,
        run_id: int,
        *,
        current_segments: list[SourceSegment] | None = None,
        edited_text: str | None = None,
    ) -> NormalizationRun:
        run = self.runs.get(run_id)
        if run is None:
            raise ContentNotFoundError(f"Запуск нормализации не найден: {run_id}")
        if run.status != NormalizationRunStatus.REVIEW_REQUIRED:
            raise NormalizationError("Применить можно только результат, ожидающий проверки")
        with self.content_service.activity(
            "transcript-normalization-apply",
            lesson_id=run.lesson_id,
            exclusive=True,
        ):
            if current_segments is None:
                current_segments, _ = self.lesson_source_segments(run.lesson_id)
            current_hash = source_sha256(sort_segments(current_segments))
            if current_hash != run.source_sha256:
                self.runs.finish(run_id, NormalizationRunStatus.STALE)
                raise SourceTranscriptChangedError(
                    "Исходный транскрипт был изменён после запуска нормализации. "
                    "Запустите нормализацию повторно."
                )
            if not run.artifact_path:
                raise NormalizationError("JSON-артефакт нормализации отсутствует")
            artifact = self.content_service.workspace / run.artifact_path
            transcript = NormalizedTranscript.model_validate_json(artifact.read_text(encoding="utf-8"))
            current_revision = self.content_service.repository.current_transcript(run.lesson_id)
            self.content_service.save_transcript(
                run.lesson_id,
                edited_text if edited_text is not None else transcript.educational_text,
                created_by=f"ollama:{run.model}",
                expected_revision_number=(current_revision.revision_number if current_revision else None),
            )
            lesson = self.content_service.get_lesson(run.lesson_id).lesson
            lesson.transition(JobStatus.READY, force=True)
            self.content_service.persist_pipeline_lesson(
                lesson,
                frozenset({"status", "error"}),
                force_status=True,
            )
            return self.runs.finish(run_id, NormalizationRunStatus.APPROVED)

    def reject_result(self, run_id: int) -> NormalizationRun:
        run = self.runs.get(run_id)
        if run is None:
            raise ContentNotFoundError(f"Запуск нормализации не найден: {run_id}")
        if run.status not in {
            NormalizationRunStatus.PENDING,
            NormalizationRunStatus.RUNNING,
            NormalizationRunStatus.REVIEW_REQUIRED,
            NormalizationRunStatus.FAILED,
        }:
            raise NormalizationError("Этот результат уже нельзя отклонить")
        return self.runs.finish(run_id, NormalizationRunStatus.CANCELLED)
