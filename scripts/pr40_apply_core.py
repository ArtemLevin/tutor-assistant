from __future__ import annotations

from pathlib import Path


def patch(path: str, old: str, new: str, *, count: int = 1) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f"{path}: marker count {actual}, expected {count}")
    file.write_text(text.replace(old, new), encoding="utf-8")


patch("src/tutor_assistant/config.py", "from typing import Literal\n", "from typing import Any, Literal\n")
patch(
    "src/tutor_assistant/config.py",
    "    max_attempts: int = Field(default=2, ge=1, le=5)\n",
    "    retry_requests: int = Field(default=0, ge=0, le=3)\n",
)
patch(
    "src/tutor_assistant/config.py",
    '    @field_validator("provider")\n',
    '''    @model_validator(mode="before")
    @classmethod
    def migrate_retry_requests(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "max_attempts" not in value or "retry_requests" in value:
            return value
        migrated = dict(value)
        legacy_attempts = int(migrated.pop("max_attempts"))
        migrated["retry_requests"] = max(0, min(3, legacy_attempts - 1))
        return migrated

    @property
    def max_attempts(self) -> int:
        return self.retry_requests + 1

    @field_validator("provider")
''',
)
patch(
    "config/app.example.yaml",
    "  request_timeout_seconds: 600\n  max_attempts: 2\n  retry_backoff_seconds: 2\n",
    "  request_timeout_seconds: 600\n  # Повторные запросы после отклонённого ответа модели: 0..3.\n"
    "  retry_requests: 0\n  retry_backoff_seconds: 2\n",
)
patch(
    "src/tutor_assistant/normalization/models.py",
    "    provider_requests: int = Field(default=0, ge=0)\n",
    "    provider_requests: int = Field(default=0, ge=0)\n"
    "    source_fallback_chunks: int = Field(default=0, ge=0)\n",
)
patch(
    "src/tutor_assistant/normalization/service.py",
    '''RETRYABLE_OUTPUT_ERRORS = (
    InvalidPlainTextOutputError,
    UnsafeNormalizationResultError,
    OllamaTimeoutError,
)
''',
    '''RETRYABLE_VALIDATION_ERRORS = (
    InvalidPlainTextOutputError,
    UnsafeNormalizationResultError,
)
RETRYABLE_PROVIDER_ERRORS = (OllamaTimeoutError,)
''',
)
patch(
    "src/tutor_assistant/normalization/service.py",
    '''                reused_chunks,
                provider_requests,
            ) = self._normalize_chunks(
''',
    '''                reused_chunks,
                provider_requests,
                source_fallback_chunks,
            ) = self._normalize_chunks(
''',
)
patch(
    "src/tutor_assistant/normalization/service.py",
    "                provider_requests=provider_requests,\n            )\n",
    "                provider_requests=provider_requests,\n"
    "                source_fallback_chunks=source_fallback_chunks,\n            )\n",
)
patch(
    "src/tutor_assistant/normalization/service.py",
    "    ) -> tuple[list[str], ValidationState, int, int, int]:\n"
    "        normalized_chunks: list[str] = []\n",
    "    ) -> tuple[list[str], ValidationState, int, int, int, int]:\n"
    "        normalized_chunks: list[str] = []\n",
)
patch(
    "src/tutor_assistant/normalization/service.py",
    "        provider_requests = 0\n        total_chunks = len(chunks)\n",
    "        provider_requests = 0\n        source_fallback_chunks = 0\n"
    "        total_chunks = len(chunks)\n",
)
patch(
    "src/tutor_assistant/normalization/service.py",
    '''                except RETRYABLE_OUTPUT_ERRORS as exc:
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
''',
    '''                except RETRYABLE_VALIDATION_ERRORS as exc:
                    if audit_event_id:
                        self.cloud_audit.finish_request(
                            audit_event_id,
                            event="request_failed",
                            error_code=type(exc).__name__,
                        )
                    if attempt + 1 < self.config.max_attempts:
                        if run is not None:
                            self.checkpoints.fail(
                                run.id or 0,
                                chunk.index,
                                f"{type(exc).__name__}: {exc}",
                            )
                        validation_errors = (f"{type(exc).__name__}: {exc}",)
                        if self.config.retry_backoff_seconds:
                            sleep(self.config.retry_backoff_seconds)
                        continue

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
                except RETRYABLE_PROVIDER_ERRORS as exc:
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
                        raise
                    if self.config.retry_backoff_seconds:
                        sleep(self.config.retry_backoff_seconds)
''',
)
patch(
    "src/tutor_assistant/normalization/service.py",
    "        return normalized_chunks, state, attempts_total, reused_chunks, provider_requests\n",
    '''        return (
            normalized_chunks,
            state,
            attempts_total,
            reused_chunks,
            provider_requests,
            source_fallback_chunks,
        )
''',
)
patch(
    "src/tutor_assistant/normalization/service.py",
    "        provider_requests: int,\n    ) -> NormalizedTranscript:\n",
    "        provider_requests: int,\n        source_fallback_chunks: int,\n"
    "    ) -> NormalizedTranscript:\n",
)
patch(
    "src/tutor_assistant/normalization/service.py",
    "                provider_requests=provider_requests,\n            ),\n",
    "                provider_requests=provider_requests,\n"
    "                source_fallback_chunks=source_fallback_chunks,\n            ),\n",
)
