from __future__ import annotations

import hashlib
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
from ..security.cloud_consent import (
    CloudAuditStore,
    CloudConsentReceipt,
    CloudProcessingRequest,
    validate_cloud_consent,
)
from .artifacts import (
    NormalizationRunStore,
    configuration_hash,
    source_sha256,
    write_json_atomic,
    write_text_atomic,
)
from .checkpoints import NormalizationCheckpointStore
from .chunking import chunk_segments, sort_segments
from .errors import (
    CloudProcessingConsentRequiredError,
    InvalidPlainTextOutputError,
    NormalizationCancelledError,
    NormalizationCheckpointMismatchError,
    NormalizationError,
    NormalizationResumeConfirmationRequired,
    OllamaTimeoutError,
    SourceTranscriptChangedError,
    UnsafeNormalizationResultError,
)
from .models import (
    NormalizationChunkRequest,
    NormalizationChunkStatus,
    NormalizationExecution,
    NormalizationManifest,
    NormalizationProgress,
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
ProgressCallback = Callable[[NormalizationProgress], None]
RETRYABLE_OUTPUT_ERRORS = (
    InvalidPlainTextOutputError,
    UnsafeNormalizationResultError,
    OllamaTimeoutError,
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
        self.checkpoints = NormalizationCheckpointStore(content_service.repository)
        self.cloud_audit = CloudAuditStore(content_service.repository)

    def recover_interrupted(self) -> int:
        if self.content_service.active_activities():
            return 0
        with self.content_service.activity(
            "transcript-normalization-recovery",
            exclusive=True,
        ):
            self.checkpoints.recover_interrupted()
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

    def cloud_processing_request(
        self,
        lesson_id: str,
        *,
        model: str | None = None,
        source_segments: list[SourceSegment] | None = None,
    ) -> CloudProcessingRequest:
        if self.config.provider != "yandex_ai_studio":
            raise NormalizationError("Cloud consent требуется только для Yandex AI Studio")
        lesson = self.content_service.repository.get_lesson(lesson_id)
        if lesson is None:
            raise ContentNotFoundError(f"Занятие не найдено: {lesson_id}")
        subject_profile = resolve_subject_profile(lesson.subject)
        selected_model = model or self.config.effective_model
        if source_segments is None:
            source_segments, _artifact = self.lesson_source_segments(lesson_id)
        else:
            source_segments = sort_segments(source_segments)
        source_hash = source_sha256(source_segments)
        config_hash = configuration_hash(
            self._configuration_payload(
                selected_model,
                lesson_subject=lesson.subject,
                subject_profile=subject_profile,
            )
        )
        chunks = chunk_segments(
            source_segments,
            max_segments=self.config.max_segments_per_chunk,
            max_characters=self.config.max_input_characters,
            overlap_segments=self.config.context_overlap_segments,
        )
        return CloudProcessingRequest(
            model=selected_model,
            source_sha256=source_hash,
            configuration_hash=config_hash,
            prompt_version=subject_profile.prompt_version,
            subject_profile=subject_profile.name.value,
            segment_count=len(source_segments),
            character_count=sum(len(item.text) for item in source_segments),
            chunk_count=len(chunks),
        )

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
        progress: ProgressCallback | None = None,
        retry_indeterminate: bool = False,
        cloud_consent: CloudConsentReceipt | None = None,
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
                progress=progress,
                retry_indeterminate=retry_indeterminate,
                cloud_consent=cloud_consent,
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
        progress: ProgressCallback | None,
        retry_indeterminate: bool,
        cloud_consent: CloudConsentReceipt | None,
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
                provider=self.config.provider,
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
            run = self.runs.mark_running(run.id or 0, resumed=not created)

        provider = self.provider_factory(self.config, model)
        started_at = datetime.now(UTC)
        started = perf_counter()
        try:
            cancellation.raise_if_cancelled()
            chunks = chunk_segments(
                source_segments,
                max_segments=self.config.max_segments_per_chunk,
                max_characters=self.config.max_input_characters,
                overlap_segments=self.config.context_overlap_segments,
            )
            consent_id: str | None = None
            if self.config.provider == "yandex_ai_studio":
                request = CloudProcessingRequest(
                    model=model,
                    source_sha256=source_hash,
                    configuration_hash=config_hash,
                    prompt_version=prompt_version,
                    subject_profile=subject_profile.name.value,
                    segment_count=len(source_segments),
                    character_count=sum(len(item.text) for item in source_segments),
                    chunk_count=len(chunks),
                )
                try:
                    validate_cloud_consent(
                        request,
                        cloud_consent,
                        policy=self.config.effective_cloud_policy,
                    )
                except PermissionError as exc:
                    raise CloudProcessingConsentRequiredError(str(exc)) from None
                consent_id = self.cloud_audit.record_consent(
                    cloud_consent,
                    request,
                    lesson_id=lesson_id,
                    run_id=run.id if run else None,
                )
            provider.check_available(model)
            (
                normalized_chunks,
                validation,
                attempts,
                reused_chunks,
                provider_requests,
            ) = self._normalize_chunks(
                lesson_id,
                chunks,
                provider,
                model,
                run,
                cancellation,
                lesson_subject=lesson.subject,
                subject_profile=subject_profile,
                configuration_hash=config_hash,
                progress=progress,
                retry_indeterminate=retry_indeterminate,
                consent_id=consent_id,
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
                reused_chunks=reused_chunks,
                provider_requests=provider_requests,
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
                    completed_chunks=len(chunks),
                    reused_chunks=reused_chunks,
                    provider_requests=provider_requests,
                    resume_count=persisted_run.resume_count if persisted_run else 0,
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
                "subject_profile=%s chunks=%d segments=%d reused_chunks=%d "
                "provider_requests=%d elapsed_seconds=%.3f retained_ratio=%.3f",
                lesson_id,
                self.config.provider,
                model,
                subject_profile.name.value,
                len(chunks),
                len(source_segments),
                reused_chunks,
                provider_requests,
                perf_counter() - started,
                transcript.statistics.retained_ratio,
            )
            return NormalizationExecution(
                run=run,
                transcript=transcript,
                artifact_path=str(artifact_path),
                manifest_path=str(manifest_path) if manifest_path else None,
            )
        except NormalizationResumeConfirmationRequired as exc:
            if run is not None:
                self.runs.finish(
                    run.id or 0,
                    NormalizationRunStatus.PENDING,
                    error=str(exc),
                )
            raise
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

    def _emit_progress(
        self,
        callback: ProgressCallback | None,
        payload: NormalizationProgress,
    ) -> None:
        if callback is None:
            return
        try:
            callback(payload)
        except Exception:
            logging.exception("event=content_filter_progress_callback_failed")

    def _normalize_chunks(
        self,
        lesson_id: str,
        chunks,
        provider: NormalizationProvider,
        model: str,
        run: NormalizationRun | None,
        cancellation: CancellationToken,
        *,
        lesson_subject: str,
        subject_profile: SubjectProfile,
        configuration_hash: str,
        progress: ProgressCallback | None,
        retry_indeterminate: bool,
        consent_id: str | None,
    ) -> tuple[list[str], ValidationState, int, int, int]:
        normalized_chunks: list[str] = []
        state = ValidationState()
        attempts_total = 0
        reused_chunks = 0
        provider_requests = 0
        total_chunks = len(chunks)
        checkpoints = {}
        if run is not None:
            prepared = self.checkpoints.prepare_chunks(
                run.id or 0,
                chunks,
                configuration_hash=configuration_hash,
                prompt_version=subject_profile.prompt_version,
                subject_profile=subject_profile.name.value,
            )
            checkpoints = {item.chunk_index: item for item in prepared}
            indeterminate = tuple(
                item.chunk_index
                for item in prepared
                if item.status == NormalizationChunkStatus.INDETERMINATE
            )
            if indeterminate:
                if self.config.provider == "yandex_ai_studio" and not retry_indeterminate:
                    logging.warning(
                        "event=content_filter_resume_confirmation_required lesson_id=%s "
                        "run_id=%s chunks=%s",
                        lesson_id,
                        run.id,
                        ",".join(str(item) for item in indeterminate),
                    )
                    raise NormalizationResumeConfirmationRequired(run.id or 0, indeterminate)
                for chunk_index in indeterminate:
                    if consent_id and retry_indeterminate:
                        self.cloud_audit.record_retry_confirmation(
                            consent_id=consent_id,
                            run_id=run.id,
                            chunk_index=chunk_index,
                            provider=self.config.provider,
                            model=model,
                        )
                    self.checkpoints.reset_indeterminate(run.id or 0, chunk_index)
                    checkpoint = self.checkpoints.get(run.id or 0, chunk_index)
                    if checkpoint is not None:
                        checkpoints[chunk_index] = checkpoint

        completed_count = 0
        for chunk in chunks:
            checkpoint = checkpoints.get(chunk.index)
            if checkpoint and checkpoint.status == NormalizationChunkStatus.COMPLETED:
                self.checkpoints.verify_completed(checkpoint)
                normalized_chunks.append(checkpoint.normalized_text or "")
                if checkpoint.quality is None:
                    raise NormalizationCheckpointMismatchError(
                        f"Checkpoint чанка {chunk.index} не содержит quality"
                    )
                state.merge(ValidationState.from_quality(checkpoint.quality))
                reused_chunks += 1
                completed_count += 1
                logging.info(
                    "event=content_filter_chunk_reused lesson_id=%s run_id=%s "
                    "chunk_index=%d total_chunks=%d",
                    lesson_id,
                    run.id if run else "dry-run",
                    chunk.index,
                    total_chunks,
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
                        state="reused",
                    ),
                )
                continue

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
                provider_requests += 1
                if run is not None:
                    self.runs.increment_attempts(run.id or 0)
                    self.checkpoints.mark_running(run.id or 0, chunk.index)
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
                        state="running",
                    ),
                )
                logging.info(
                    "event=content_filter_chunk_started lesson_id=%s run_id=%s "
                    "chunk_index=%d total_chunks=%d attempt=%d",
                    lesson_id,
                    run.id if run else "dry-run",
                    chunk.index,
                    total_chunks,
                    attempt + 1,
                )
                audit_event_id: str | None = None
                if consent_id:
                    request_fingerprint = hashlib.sha256(
                        f"{consent_id}:{run.id if run else 'dry-run'}:"
                        f"{chunk.index}:{attempt + 1}".encode("utf-8")
                    ).hexdigest()
                    audit_event_id = self.cloud_audit.request_started(
                        consent_id=consent_id,
                        run_id=run.id if run else None,
                        chunk_index=chunk.index,
                        provider=self.config.provider,
                        model=model,
                        request_fingerprint=request_fingerprint,
                    )
                try:
                    response = provider.normalize_chunk(
                        request,
                        validation_errors=validation_errors,
                        cancellation=cancellation,
                    )
                    if audit_event_id:
                        self.cloud_audit.finish_request(
                            audit_event_id,
                            event="request_completed",
                            response_sha256=hashlib.sha256(response.encode("utf-8")).hexdigest(),
                        )
                    candidate_state = ValidationState()
                    normalized = validate_plain_text_response(
                        chunk.segments,
                        chunk.target_ids,
                        response,
                        candidate_state,
                        subject_profile=subject_profile.name.value,
                    )
                    if run is not None:
                        self.checkpoints.complete(
                            run.id or 0,
                            chunk.index,
                            normalized_text=normalized,
                            quality=candidate_state.quality(),
                        )
                    normalized_chunks.append(normalized)
                    state.merge(candidate_state)
                    completed_count += 1
                    logging.info(
                        "event=content_filter_chunk_completed lesson_id=%s run_id=%s "
                        "chunk_index=%d total_chunks=%d attempt=%d",
                        lesson_id,
                        run.id if run else "dry-run",
                        chunk.index,
                        total_chunks,
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
                            state="completed",
                        ),
                    )
                    break
                except NormalizationCancelledError:
                    if audit_event_id:
                        self.cloud_audit.finish_request(
                            audit_event_id,
                            event="request_indeterminate",
                            error_code="NormalizationCancelledError",
                        )
                    if run is not None:
                        self.checkpoints.reset_pending(run.id or 0, chunk.index)
                    raise
                except RETRYABLE_OUTPUT_ERRORS as exc:
                    if audit_event_id:
                        self.cloud_audit.finish_request(
                            audit_event_id,
                            event="request_failed",
                            error_code=type(exc).__name__,
                        )
                    if run is not None:
                        self.checkpoints.fail(
                            run.id or 0,
                            chunk.index,
                            f"{type(exc).__name__}: {exc}",
                        )
                    if attempt + 1 >= self.config.max_attempts:
                        logging.warning(
                            "event=content_filter_chunk_failed lesson_id=%s run_id=%s "
                            "chunk_index=%d error_code=%s",
                            lesson_id,
                            run.id if run else "dry-run",
                            chunk.index,
                            type(exc).__name__,
                        )
                        raise
                    validation_errors = (f"{type(exc).__name__}: {exc}",)
                    if self.config.retry_backoff_seconds:
                        sleep(self.config.retry_backoff_seconds)
                except Exception as exc:
                    if audit_event_id:
                        self.cloud_audit.finish_request(
                            audit_event_id,
                            event=(
                                "request_indeterminate"
                                if self.config.provider == "yandex_ai_studio"
                                else "request_failed"
                            ),
                            error_code=type(exc).__name__,
                        )
                    if run is not None:
                        error = f"{type(exc).__name__}: {exc}"
                        if self.config.provider == "yandex_ai_studio":
                            self.checkpoints.mark_indeterminate(
                                run.id or 0,
                                chunk.index,
                                error,
                            )
                            logging.warning(
                                "event=content_filter_chunk_indeterminate lesson_id=%s "
                                "run_id=%s chunk_index=%d error_code=%s",
                                lesson_id,
                                run.id,
                                chunk.index,
                                type(exc).__name__,
                            )
                        else:
                            self.checkpoints.fail(run.id or 0, chunk.index, error)
                    raise
            else:
                raise InvalidPlainTextOutputError("Не удалось проверить plain-text ответ блока")
        return normalized_chunks, state, attempts_total, reused_chunks, provider_requests

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
        reused_chunks: int,
        provider_requests: int,
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
                completed_chunks=chunk_count,
                reused_chunks=reused_chunks,
                provider_requests=provider_requests,
            ),
            quality=validation.quality(),
        )

    def _source_hash_for_test(self, segments: list[SourceSegment]) -> str:
        return source_sha256(sort_segments(segments))

    def _subject_profile_for_test(self, subject: str) -> SubjectProfile:
        return resolve_subject_profile(subject)

    def _config_hash_for_test(
        self,
        model: str,
        lesson_subject: str,
        subject_profile: SubjectProfile,
    ) -> str:
        return configuration_hash(
            self._configuration_payload(
                model,
                lesson_subject=lesson_subject,
                subject_profile=subject_profile,
            )
        )

    def _chunks_for_test(self, segments: list[SourceSegment]):
        return chunk_segments(
            segments,
            max_segments=self.config.max_segments_per_chunk,
            max_characters=self.config.max_input_characters,
            overlap_segments=self.config.context_overlap_segments,
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
