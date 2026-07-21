from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Generic, TypeVar

from ..config import LatexConfig, RepositoryConfig
from ..content import ContentBusyError, StudentContentService
from ..domain import Lesson
from ..latex.remote import RemoteCompilationResult, RemoteLatexService

T = TypeVar("T")


class BackgroundTaskState(StrEnum):
    COMPLETED = "completed"
    NO_CHANGES = "no_changes"
    SKIPPED_BUSY = "skipped_busy"


@dataclass(frozen=True, slots=True)
class BackgroundTaskResult(Generic[T]):
    state: BackgroundTaskState
    payload: T | None = None
    reason: str | None = None
    blocking_activity: str | None = None
    manually_requested: bool = False

    @classmethod
    def completed(
        cls,
        payload: T,
        *,
        manually_requested: bool = False,
    ) -> BackgroundTaskResult[T]:
        return cls(
            state=BackgroundTaskState.COMPLETED,
            payload=payload,
            manually_requested=manually_requested,
        )

    @classmethod
    def no_changes(
        cls,
        *,
        manually_requested: bool = False,
    ) -> BackgroundTaskResult[T]:
        return cls(
            state=BackgroundTaskState.NO_CHANGES,
            manually_requested=manually_requested,
        )

    @classmethod
    def skipped_busy(
        cls,
        reason: str,
        *,
        blocking_activity: str | None = None,
        manually_requested: bool = False,
    ) -> BackgroundTaskResult[T]:
        return cls(
            state=BackgroundTaskState.SKIPPED_BUSY,
            reason=reason,
            blocking_activity=blocking_activity,
            manually_requested=manually_requested,
        )


def run_latex_monitor_scan(
    content_service: StudentContentService,
    repository: RepositoryConfig,
    latex: LatexConfig,
    lessons: Iterable[Lesson],
    cache_dir_for: Callable[[Lesson], Path],
    *,
    manually_requested: bool = False,
) -> BackgroundTaskResult[RemoteCompilationResult]:
    """Run one LaTeX monitor iteration and classify expected lease contention."""

    try:
        with content_service.activity("latex-monitor"):
            service = RemoteLatexService(repository, latex)
            for lesson in lessons:
                if service.is_ready(lesson):
                    return BackgroundTaskResult.completed(
                        service.compile_lesson(
                            lesson,
                            cache_dir=cache_dir_for(lesson),
                        ),
                        manually_requested=manually_requested,
                    )
    except ContentBusyError as exc:
        blocker = next(
            (
                activity.activity
                for activity in content_service.active_activities()
                if activity.exclusive
            ),
            None,
        )
        logging.info(
            "Проверка LaTeX отложена: blocker=%s details=%s",
            blocker or "unknown",
            exc,
        )
        return BackgroundTaskResult.skipped_busy(
            str(exc),
            blocking_activity=blocker,
            manually_requested=manually_requested,
        )

    return BackgroundTaskResult.no_changes(
        manually_requested=manually_requested,
    )
