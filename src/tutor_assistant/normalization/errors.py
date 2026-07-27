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
