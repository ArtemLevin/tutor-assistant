from __future__ import annotations


class NormalizationError(Exception):
    """Base error for transcript normalization."""


class OllamaUnavailableError(NormalizationError):
    pass


class OllamaModelMissingError(NormalizationError):
    pass


class OllamaTimeoutError(NormalizationError):
    pass


class InvalidStructuredOutputError(NormalizationError):
    pass


class IncompleteSegmentClassificationError(NormalizationError):
    pass


class SourceTranscriptChangedError(NormalizationError):
    pass


class UnsafeNormalizationResultError(NormalizationError):
    pass


class NormalizationCancelledError(NormalizationError):
    pass
