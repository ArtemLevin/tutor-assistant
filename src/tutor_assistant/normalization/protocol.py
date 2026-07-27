from __future__ import annotations

from collections.abc import Callable, Iterable
from threading import Event
from typing import Protocol

from .errors import NormalizationCancelledError
from .models import NormalizationChunkRequest, NormalizationDiagnostics
from .prompts import render_target_text


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
    ) -> str: ...

    def diagnose(self) -> NormalizationDiagnostics: ...


FakeResponse = str | Exception | Callable[[NormalizationChunkRequest], str]


class FakeNormalizationProvider:
    """Scriptable plain-text provider used by unit tests."""

    def __init__(
        self,
        responses: Iterable[FakeResponse] = (),
        *,
        default: Callable[[NormalizationChunkRequest], str] | None = None,
    ) -> None:
        self.responses = list(responses)
        self.default = default or self._keep_all
        self.requests: list[NormalizationChunkRequest] = []

    @staticmethod
    def _keep_all(request: NormalizationChunkRequest) -> str:
        return render_target_text(request.segments)

    def check_available(self, model: str) -> None:
        if not model:
            raise ValueError("model is required")

    def normalize_chunk(
        self,
        request: NormalizationChunkRequest,
        *,
        validation_errors: tuple[str, ...] = (),
        cancellation: CancellationToken | None = None,
    ) -> str:
        del validation_errors
        if cancellation:
            cancellation.raise_if_cancelled()
        self.requests.append(request)
        response = self.responses.pop(0) if self.responses else self.default
        if isinstance(response, Exception):
            raise response
        return response(request) if callable(response) else response

    def diagnose(self) -> NormalizationDiagnostics:
        return NormalizationDiagnostics(
            provider="fake",
            endpoint="memory",
            endpoint_local=True,
            reachable=True,
            model_available=True,
            plain_text_valid=True,
        )
