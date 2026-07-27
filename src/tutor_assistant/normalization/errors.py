from __future__ import annotations


class NormalizationError(Exception):
    """Base error for transcript normalization."""


class OllamaUnavailableError(NormalizationError):
    pass


class OllamaModelMissingError(NormalizationError):
    pass


class OllamaTimeoutError(NormalizationError):
    pass


class YandexAIStudioUnavailableError(NormalizationError):
    pass


class YandexAIStudioAuthenticationError(NormalizationError):
    pass


class YandexAIStudioTimeoutError(NormalizationError):
    pass


class InvalidPlainTextOutputError(NormalizationError):
    pass


class InvalidStructuredOutputError(NormalizationError):
    """Deprecated compatibility name for pre-v2 structured responses."""

    pass


class IncompleteSegmentClassificationError(NormalizationError):
    pass


class SourceTranscriptChangedError(NormalizationError):
    pass


class UnsafeNormalizationResultError(NormalizationError):
    pass


class NormalizationCancelledError(NormalizationError):
    pass


class NormalizationCheckpointMismatchError(NormalizationError):
    pass


class NormalizationResumeConfirmationRequired(NormalizationError):
    def __init__(self, run_id: int, chunk_indices: tuple[int, ...]) -> None:
        self.run_id = run_id
        self.chunk_indices = chunk_indices
        numbers = ", ".join(str(index + 1) for index in chunk_indices)
        super().__init__(
            "Предыдущий облачный запрос был прерван в неопределённом состоянии. "
            f"Повторная отправка блоков {numbers} может привести к повторному списанию."
        )
