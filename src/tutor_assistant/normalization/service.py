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
    write_text_atomic,
)
from .chunking import chunk_segments, sort_segments
from .errors import (
    InvalidPlainTextOutputError,
    NormalizationCancelledError,
    NormalizationError,
    OllamaTimeoutError,
    SourceTranscriptChangedError,
    UnsafeNormalizationResultError,
    YandexAIStudioTimeoutError,
)
from .models import (
    NormalizationChunkRequest,
    NormalizationExecution,
    NormalizationManifest,
    NormalizationRun,
    NormalizationRunStatus,
    NormalizationStatistics,
    NormalizedTranscript,
    SourceSegment,
)
from .ollama_client import OllamaClient
from .prompts import render_target_text
from .protocol import CancellationToken, NormalizationProvider
from .subjects import SubjectProfile, resolve_subject_profile
from .validation import ValidationState, validate_plain_text_response
from .yandex_client import YandexAIStudioClient

ProviderFactory = Callable[[NormalizationConfig, str], NormalizationProvider]
RETRYABLE_OUTPUT_ERRORS = (
    InvalidPlainTextOutputError,
    UnsafeNormalizationResultError,
    OllamaTimeoutError,
    YandexAIStudioTimeoutError,
)


def build_provider(
    config: NormalizationConfig,
    model: str | None = None,
) -> NormalizationProvider:
    selected = model or config.effective_model
    if config.provider == "yandex_ai_studio":
        return YandexAIStudioClient(config, model=selected)
    return OllamaClient(config, model=selected)


class NormalizationService:
    def __init__(
        self,
        config: NormalizationConfig,
        content_service: StudentContentService,
        *,
        provider_factory: ProviderFactory = build_provider,
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

    def _configuration_payload(
        self,
        model: str,
        *,
        lesson_subject: str,
        subject_profile: SubjectProfile,
    ) -> dict[str, Any]:
        payload = self.config.model_dump(mode="json")
        payload["model"] = model
        payload["lesson_subject"] = lesson_subject.strip()
        payload["subject_profile"] = subject_profile.name.value
        payload["prompt_version"] = subject_profile.prompt_version
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
        del include_removed_text
        if not self.config.enabled and not dry_run:
            raise NormalizationError("LLM-фильтрация учебного содержания отключена в конфигурации")
        selected_model = model or self.config.effective_model
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
        source_segments: list[SourceSegment] | None,
        source_artifact: str | None,
        cancellation: CancellationToken,
    ) -> NormalizationExecution:
        lesson = self.content_service.repository.get_lesson(lesson_id)
        if lesson is None:
            raise ContentNotFoundError(f"Занятие не найдено: {lesson_id}")
        subject_profile = resolve_subject_profile(lesson.subject)
        prompt_version = subject_profile.prompt_version
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
        config_hash = configuration_hash(
            self._configuration_payload(
                model,
                lesson_subject=lesson.subject,
                subject_profile=subject_profile,
            )
        )
        run: NormalizationRun | None = None
        if not dry_run:
            run, created = self.runs.create_or_get(
                lesson_id=lesson_id,
                source_hash=source_hash,
                model=model,
                prompt_version=prompt_version,
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
                result = self.load_result(run)
                return NormalizationExecution(
                    run=run,
                    transcript=result,
                    artifact_path=str(self._artifact_path(run)),
                    manifest_path=str(self._manifest_path(run)),
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
            normalized_chunks, validation, attempts = self._normalize_chunks(
                lesson_id,
                chunks,
                provider,
                run,
                cancellation,
                lesson_subject=lesson.subject,
                subject_profile=subject_profile,
            )
            educational_text = "\n".join(part for part in normalized_chunks if part).strip()
            transcript = self._build_result(
                lesson_id=lesson_id,
                source_segments=source_segments,
                educational_text=educational_text,
                source_artifact=source_artifact,
                source_hash=source_hash,
                model=model,
                chunk_count=len(chunks),
                validation=validation,
                created_at=datetime.now(UTC),
                lesson_subject=lesson.subject,
                subject_profile=subject_profile,
                prompt_version=prompt_version,
            )
            cancellation.raise_if_cancelled()
            if output is None:
                if dry_run:
                    directory = Path(tempfile.mkdtemp(prefix="tutor-normalization-"))
                    artifact_path = directory / "transcript_normalized.txt"
                else:
                    artifact_path = (
                        self.content_service.workspace
                        / "lessons"
                        / lesson_id
                        / "transcript"
                        / "transcript_normalized.txt"
                    )
            else:
                artifact_path = output.resolve()
            write_text_atomic(artifact_path, educational_text)

            manifest_path: Path | None = None
            if not dry_run and run is not None:
                completed_at = datetime.now(UTC)
                persisted_run = self.runs.get(run.id or 0)
                manifest_path = artifact_path.with_name("normalization_manifest.json")
                manifest = NormalizationManifest(
                    provider=self.config.provider,
                    model=model,
                    prompt_version=prompt_version,
                    source_artifact=source_artifact,
                    source_sha256=source_hash,
                    configuration_hash=config_hash,
                    started_at=started_at,
                    completed_at=completed_at,
                    elapsed_seconds=round(perf_counter() - started, 3),
                    chunk_count=len(chunks),
                    attempts=persisted_run.attempts if persisted_run else attempts,
                    status=NormalizationRunStatus.REVIEW_REQUIRED,
                    statistics=transcript.statistics,
                    quality=transcript.quality,
                    lesson_subject=lesson.subject,
                    subject_profile=subject_profile.name.value,
                )
                write_json_atomic(manifest_path, manifest)
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
                current.artifacts.normalized_transcript_text = str(artifact_path.resolve())
                current.artifacts.normalized_transcript_json = None
                current.artifacts.normalization_manifest = str(manifest_path.resolve())
                self.content_service.persist_pipeline_lesson(
                    current,
                    frozenset({"artifacts"}),
                )
            logging.info(
                "event=content_filter_completed lesson_id=%s provider=%s model=%s "
                "subject_profile=%s chunks=%d segments=%d elapsed_seconds=%.3f "
                "retained_ratio=%.3f",
                lesson_id,
                self.config.provider,
                model,
                subject_profile.name.value,
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
                "event=content_filter_failed lesson_id=%s provider=%s model=%s error_code=%s",
                lesson_id,
                self.config.provider,
                model,
                type(exc).__name__,
            )
            raise

    def _normalize_chunks(
        self,
        lesson_id: str,
        chunks,
        provider: NormalizationProvider,
        run: NormalizationRun | None,
        cancellation: CancellationToken,
        *,
        lesson_subject: str,
        subject_profile: SubjectProfile,
    ) -> tuple[list[str], ValidationState, int]:
        normalized_chunks: list[str] = []
        state = ValidationState()
        attempts_total = 0
        for chunk in chunks:
            request = NormalizationChunkRequest(
                lesson_id=lesson_id,
                prompt_version=subject_profile.prompt_version,
                mode=self.config.mode,
                segments=list(chunk.segments),
                lesson_subject=lesson_subject,
                subject_profile=subject_profile.name.value,
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
                    normalized = validate_plain_text_response(
                        chunk.segments,
                        chunk.target_ids,
                        response,
                        candidate_state,
                        subject_profile=subject_profile.name.value,
                    )
                    normalized_chunks.append(normalized)
                    state.merge(candidate_state)
                    break
                except RETRYABLE_OUTPUT_ERRORS as exc:
                    if attempt + 1 >= self.config.max_attempts:
                        raise
                    validation_errors = (f"{type(exc).__name__}: {exc}",)
                    if self.config.retry_backoff_seconds:
                        sleep(self.config.retry_backoff_seconds)
            else:
                raise InvalidPlainTextOutputError("Не удалось проверить plain-text ответ блока")
        return normalized_chunks, state, attempts_total

    def _build_result(
        self,
        *,
        lesson_id: str,
        source_segments: list[SourceSegment],
        educational_text: str,
        source_artifact: str,
        source_hash: str,
        model: str,
        chunk_count: int,
        validation: ValidationState,
        created_at: datetime,
        lesson_subject: str,
        subject_profile: SubjectProfile,
        prompt_version: str,
    ) -> NormalizedTranscript:
        source_text = render_target_text(source_segments)
        source_characters = len(source_text)
        normalized_characters = len(educational_text)
        retained_ratio = normalized_characters / source_characters if source_characters else 1.0
        if 1 - retained_ratio > self.config.high_removal_threshold:
            validation.requires_manual_attention = True
            validation.warnings.append("unusually_high_removal_ratio")
        return NormalizedTranscript(
            lesson_id=lesson_id,
            source={
                "artifact": source_artifact,
                "sha256": source_hash,
                "segment_count": len(source_segments),
                "language": "ru",
                "subject": lesson_subject,
            },
            normalizer={
                "provider": self.config.provider,
                "model": model,
                "prompt_version": prompt_version,
                "subject_profile": subject_profile.name.value,
                "subject_display_name": subject_profile.display_name,
                "mode": self.config.mode,
                "created_at": created_at.isoformat(),
            },
            educational_text=educational_text,
            statistics=NormalizationStatistics(
                source_characters=source_characters,
                normalized_characters=normalized_characters,
                retained_ratio=round(retained_ratio, 6),
                source_segments=len(source_segments),
                chunk_count=chunk_count,
            ),
            quality=validation.quality(),
        )

    def _artifact_path(self, run: NormalizationRun) -> Path:
        if not run.artifact_path:
            raise NormalizationError("Текстовый артефакт нормализации отсутствует")
        return self.content_service.workspace / run.artifact_path

    def _manifest_path(self, run: NormalizationRun) -> Path:
        return self._artifact_path(run).with_name("normalization_manifest.json")

    def load_result(self, run: NormalizationRun) -> NormalizedTranscript:
        artifact = self._artifact_path(run)
        manifest_path = self._manifest_path(run)
        if not artifact.is_file():
            raise NormalizationError(f"Текстовый артефакт не найден: {artifact}")
        if not manifest_path.is_file():
            raise NormalizationError(f"Manifest нормализации не найден: {manifest_path}")
        try:
            manifest = NormalizationManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise NormalizationError("Manifest нормализации повреждён") from exc
        return NormalizedTranscript(
            lesson_id=run.lesson_id,
            source={
                "artifact": manifest.source_artifact,
                "sha256": manifest.source_sha256,
                "segment_count": manifest.statistics.source_segments,
                "language": "ru",
                "subject": manifest.lesson_subject,
            },
            normalizer={
                "provider": manifest.provider,
                "model": manifest.model,
                "prompt_version": manifest.prompt_version,
                "subject_profile": manifest.subject_profile,
                "subject_display_name": resolve_subject_profile(manifest.subject_profile).display_name,
                "mode": self.config.mode,
                "created_at": manifest.completed_at.isoformat(),
            },
            educational_text=artifact.read_text(encoding="utf-8").strip(),
            statistics=manifest.statistics,
            quality=manifest.quality,
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
            result = self.load_result(run)
            content = edited_text if edited_text is not None else result.educational_text
            if not content.strip():
                raise NormalizationError("Нельзя применить пустой нормализованный транскрипт")
            current_revision = self.content_service.repository.current_transcript(run.lesson_id)
            provider = result.normalizer.get("provider", self.config.provider)
            self.content_service.save_transcript(
                run.lesson_id,
                content,
                created_by=f"{provider}:{run.model}",
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
