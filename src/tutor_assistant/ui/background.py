from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Generic, TypeVar

from ..config import LatexConfig, RepositoryConfig
from ..content import ActivityLeaseInfo, ContentBusyError, StudentContentService
from ..domain import Lesson
from ..latex.remote import RemoteCompilationResult, RemoteLatexService

T = TypeVar("T")


class BackgroundTaskState(StrEnum):
    COMPLETED = "completed"
    NO_CHANGES = "no_changes"
    SKIPPED_BUSY = "skipped_busy"
    REJECTED = "rejected"
    RETRYABLE_FAILURE = "retryable_failure"


class BackgroundTaskPurpose(StrEnum):
    LATEX_MONITOR = "latex-monitor"
    CONTENT_MAINTENANCE = "content-maintenance"
    LATEX_COMPILATION = "latex-compilation"
    CONTENT_BROWSER = "content-browser"
    DATABASE_BACKUP = "database-backup"
    CONTENT_DIAGNOSTICS = "content-diagnostics"


class BusyPolicy(StrEnum):
    FAIL = "fail"
    SKIP = "skip"
    DEFER = "defer"


class BackgroundTaskPhase(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    DEFERRED = "deferred"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BackgroundTaskSpec(Generic[T]):
    purpose: BackgroundTaskPurpose
    operation: Callable[[], T]
    activity: str | None = None
    lesson_id: str | None = None
    exclusive: bool = False
    ttl: timedelta = timedelta(minutes=2)
    busy_policy: BusyPolicy = BusyPolicy.FAIL
    manually_requested: bool = False
    defer_delay_ms: int = 500
    allow_parallel: bool = False
    none_is_no_changes: bool = False
    retry_allowed: Callable[[], bool] | None = None
    handled_exceptions: tuple[type[Exception], ...] = ()
    handled_exception_retryable: bool = False
    handled_exception_message: Callable[[Exception], str] | None = None


@dataclass(frozen=True, slots=True)
class BackgroundTaskResult(Generic[T]):
    state: BackgroundTaskState
    payload: T | None = None
    reason: str | None = None
    blockers: tuple[ActivityLeaseInfo, ...] = ()
    blocking_activity: str | None = None
    manually_requested: bool = False

    @classmethod
    def completed(
        cls,
        payload: T | None,
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
        blockers: tuple[ActivityLeaseInfo, ...] = (),
        blocking_activity: str | None = None,
        manually_requested: bool = False,
    ) -> BackgroundTaskResult[T]:
        if blocking_activity is None and blockers:
            blocking_activity = blockers[0].activity
        return cls(
            state=BackgroundTaskState.SKIPPED_BUSY,
            reason=reason,
            blockers=blockers,
            blocking_activity=blocking_activity,
            manually_requested=manually_requested,
        )

    @classmethod
    def handled_failure(
        cls,
        reason: str,
        *,
        retryable: bool = False,
        manually_requested: bool = False,
    ) -> BackgroundTaskResult[T]:
        return cls(
            state=(BackgroundTaskState.RETRYABLE_FAILURE if retryable else BackgroundTaskState.REJECTED),
            reason=reason,
            manually_requested=manually_requested,
        )


def scan_remote_latex(
    repository: RepositoryConfig,
    latex: LatexConfig,
    lessons: Iterable[Lesson],
    cache_dir_for: Callable[[Lesson], Path],
) -> RemoteCompilationResult | None:
    """Run one pure remote-LaTeX scan without acquiring a workspace lease."""

    service = RemoteLatexService(repository, latex)
    for lesson in lessons:
        if service.is_ready(lesson):
            return service.compile_lesson(
                lesson,
                cache_dir=cache_dir_for(lesson),
            )
    return None


def run_latex_monitor_scan(
    content_service: StudentContentService,
    repository: RepositoryConfig,
    latex: LatexConfig,
    lessons: Iterable[Lesson],
    cache_dir_for: Callable[[Lesson], Path],
    *,
    manually_requested: bool = False,
) -> BackgroundTaskResult[RemoteCompilationResult]:
    """Compatibility wrapper for callers that still own their lease lifecycle."""

    try:
        with content_service.activity("latex-monitor"):
            result = scan_remote_latex(repository, latex, lessons, cache_dir_for)
    except ContentBusyError as exc:
        blockers = exc.blockers or tuple(
            item for item in content_service.active_activities() if item.exclusive
        )
        return BackgroundTaskResult.skipped_busy(
            str(exc),
            blockers=blockers,
            manually_requested=manually_requested,
        )

    if result is None:
        return BackgroundTaskResult.no_changes(
            manually_requested=manually_requested,
        )
    return BackgroundTaskResult.completed(
        result,
        manually_requested=manually_requested,
    )
