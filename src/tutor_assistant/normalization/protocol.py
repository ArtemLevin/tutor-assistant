from __future__ import annotations

from collections.abc import Callable, Iterable
from threading import Event
from typing import Protocol

from .errors import NormalizationCancelledError
from .models import NormalizationChunkRequest, NormalizationChunkResponse


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise NormalizationCancelledError("Нормализация отменена пользователем")


class NormalizationProvider(Protocol):
    def check_available(self, model: str) -> None: ...

    def normalize_chunk(
        self,
        request: NormalizationChunkRequest,
        *,
        validation_errors: tuple[str, ...] = (),
        cancellation: CancellationToken | None = None,
    ) -> NormalizationChunkResponse: ...


FakeResponse = (
    NormalizationChunkResponse | Exception | Callable[[NormalizationChunkRequest], NormalizationChunkResponse]
)


class FakeNormalizationProvider:
    """Scriptable provider used by unit tests; it never contacts Ollama."""

    def __init__(
        self,
        responses: Iterable[FakeResponse] = (),
        *,
        default: Callable[[NormalizationChunkRequest], NormalizationChunkResponse] | None = None,
    ) -> None:
        self.responses = list(responses)
        self.default = default or self._keep_all
        self.requests: list[NormalizationChunkRequest] = []

    @staticmethod
    def _keep_all(request: NormalizationChunkRequest) -> NormalizationChunkResponse:
        from .models import SegmentDecision

        return NormalizationChunkResponse(
            decisions=[
                SegmentDecision(
                    source_segment_id=segment.source_segment_id,
                    action="keep",
                    normalized_text=None,
                    category="educational",
                    reason_code="conservative_keep",
                )
                for segment in request.segments
                if not segment.context_only
            ]
        )

    def check_available(self, model: str) -> None:
        if not model:
            raise ValueError("model is required")

    def normalize_chunk(
        self,
        request: NormalizationChunkRequest,
        *,
        validation_errors: tuple[str, ...] = (),
        cancellation: CancellationToken | None = None,
    ) -> NormalizationChunkResponse:
        del validation_errors
        if cancellation:
            cancellation.raise_if_cancelled()
        self.requests.append(request)
        response = self.responses.pop(0) if self.responses else self.default
        if isinstance(response, Exception):
            raise response
        return response(request) if callable(response) else response
