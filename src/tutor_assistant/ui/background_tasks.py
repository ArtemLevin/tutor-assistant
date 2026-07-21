from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass, replace
from itertools import count
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from ..content import ActivityLease, ContentBusyError, StudentContentService
from .background import (
    BackgroundTaskPhase,
    BackgroundTaskPurpose,
    BackgroundTaskResult,
    BackgroundTaskSpec,
    BackgroundTaskState,
    BusyPolicy,
)


class _TaskWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], object]) -> None:
        super().__init__()
        self.operation = operation

    def run(self) -> None:
        try:
            self.succeeded.emit(self.operation())
        except Exception:
            self.failed.emit(traceback.format_exc())


@dataclass(slots=True)
class _TaskCallbacks:
    on_success: Callable[[BackgroundTaskResult[Any]], None] | None = None
    on_busy: Callable[[BackgroundTaskResult[Any]], None] | None = None
    on_failure: Callable[[str], None] | None = None
    on_finished: Callable[[], None] | None = None


@dataclass(slots=True)
class _DeferredTask:
    spec: BackgroundTaskSpec[Any]
    callbacks: _TaskCallbacks
    result: BackgroundTaskResult[Any]


@dataclass(slots=True)
class _RunningTask:
    key: str
    spec: BackgroundTaskSpec[Any]
    callbacks: _TaskCallbacks
    worker: _TaskWorker


class BackgroundTaskCoordinator(QObject):
    """Coordinate short-lived GUI workers, workspace leases and deferred retries."""

    state_changed = Signal(str, str)

    def __init__(
        self,
        content_service: StudentContentService,
        worker_registry: list[QThread] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.content_service = content_service
        self.worker_registry = worker_registry
        self._running: dict[str, _RunningTask] = {}
        self._deferred: dict[BackgroundTaskPurpose, _DeferredTask] = {}
        self._phases: dict[BackgroundTaskPurpose, BackgroundTaskPhase] = {}
        self._scheduled_retries: set[BackgroundTaskPurpose] = set()
        self._sequence = count(1)
        self._shutdown = False

    def phase(self, purpose: BackgroundTaskPurpose) -> BackgroundTaskPhase:
        return self._phases.get(purpose, BackgroundTaskPhase.IDLE)

    def is_running(self, purpose: BackgroundTaskPurpose) -> bool:
        return any(item.spec.purpose == purpose for item in self._running.values())

    def running_count(self, purpose: BackgroundTaskPurpose | None = None) -> int:
        if purpose is None:
            return len(self._running)
        return sum(item.spec.purpose == purpose for item in self._running.values())

    def has_pending(self) -> bool:
        return bool(self._running or self._deferred or self._scheduled_retries)

    def submit(
        self,
        spec: BackgroundTaskSpec[Any],
        *,
        on_success: Callable[[BackgroundTaskResult[Any]], None] | None = None,
        on_busy: Callable[[BackgroundTaskResult[Any]], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> bool:
        callbacks = _TaskCallbacks(on_success, on_busy, on_failure, on_finished)
        if self._shutdown:
            logging.info(
                "event=background_task_rejected purpose=%s reason=shutdown",
                spec.purpose.value,
            )
            return False

        deferred = self._deferred.pop(spec.purpose, None)
        if deferred is not None:
            self._scheduled_retries.discard(spec.purpose)
            if deferred.spec.manually_requested and not spec.manually_requested:
                spec = deferred.spec
                callbacks = deferred.callbacks
            elif spec.manually_requested and not deferred.spec.manually_requested:
                spec = replace(spec, manually_requested=True)

        if not spec.allow_parallel and self.is_running(spec.purpose):
            if spec.manually_requested and callbacks.on_busy:
                result = BackgroundTaskResult[Any].skipped_busy(
                    "Операция уже выполняется",
                    manually_requested=True,
                )
                self._safe_callback(callbacks.on_busy, result, callbacks.on_failure)
            logging.info(
                "event=background_task_duplicate purpose=%s manual=%s",
                spec.purpose.value,
                spec.manually_requested,
            )
            return False

        lease: ActivityLease | None = None
        if spec.activity:
            acquisition = self.content_service.try_acquire_activity(
                spec.activity,
                lesson_id=spec.lesson_id,
                exclusive=spec.exclusive,
                ttl=spec.ttl,
            )
            if acquisition.lease is None:
                result = BackgroundTaskResult[Any].skipped_busy(
                    ContentBusyError.from_blockers(acquisition.blockers).args[0],
                    blockers=acquisition.blockers,
                    manually_requested=spec.manually_requested,
                )
                self._apply_busy(spec, callbacks, result)
                return False
            lease = acquisition.lease

        key = (
            f"{spec.purpose.value}:{next(self._sequence)}"
            if spec.allow_parallel
            else spec.purpose.value
        )

        def execute() -> BackgroundTaskResult[Any]:
            try:
                try:
                    payload = spec.operation()
                except ContentBusyError as exc:
                    blockers = exc.blockers or tuple(self.content_service.active_activities())
                    return BackgroundTaskResult[Any].skipped_busy(
                        str(exc),
                        blockers=blockers,
                        manually_requested=spec.manually_requested,
                    )
                if isinstance(payload, BackgroundTaskResult):
                    return payload
                if payload is None and spec.none_is_no_changes:
                    return BackgroundTaskResult[Any].no_changes(
                        manually_requested=spec.manually_requested,
                    )
                return BackgroundTaskResult[Any].completed(
                    payload,
                    manually_requested=spec.manually_requested,
                )
            finally:
                if lease is not None:
                    lease.release()

        worker = _TaskWorker(execute)
        task = _RunningTask(key, spec, callbacks, worker)
        worker.succeeded.connect(lambda result, task_key=key: self._task_succeeded(task_key, result))
        worker.failed.connect(lambda details, task_key=key: self._task_failed(task_key, details))
        worker.finished.connect(lambda task_key=key: self._task_finished(task_key))
        self._running[key] = task
        if self.worker_registry is not None:
            self.worker_registry.append(worker)
        self._set_phase(spec.purpose, BackgroundTaskPhase.RUNNING)
        logging.info(
            "event=background_task_started purpose=%s activity=%s manual=%s policy=%s",
            spec.purpose.value,
            spec.activity or "none",
            spec.manually_requested,
            spec.busy_policy.value,
        )
        try:
            worker.start()
        except Exception:
            self._running.pop(key, None)
            if self.worker_registry is not None and worker in self.worker_registry:
                self.worker_registry.remove(worker)
            if lease is not None:
                lease.release()
            self._set_phase(spec.purpose, BackgroundTaskPhase.FAILED)
            details = traceback.format_exc()
            self._safe_failure(callbacks.on_failure, details)
            return False
        return True

    def cancel_deferred(self, purpose: BackgroundTaskPurpose) -> None:
        self._deferred.pop(purpose, None)
        self._scheduled_retries.discard(purpose)
        if not self.is_running(purpose):
            self._set_phase(purpose, BackgroundTaskPhase.IDLE)

    def resume_deferred(self, *, released_activity: str | None = None) -> None:
        if self._shutdown:
            return
        for purpose, item in tuple(self._deferred.items()):
            blockers = item.result.blockers
            if released_activity is not None:
                local_match = any(
                    blocker.activity == released_activity
                    and blocker.owner_id == self.content_service.owner_id
                    for blocker in blockers
                )
                if not local_match:
                    continue
            elif blockers and not all(
                blocker.owner_id == self.content_service.owner_id for blocker in blockers
            ):
                continue
            self._schedule_retry(purpose, item)

    def begin_shutdown(self) -> None:
        self._shutdown = True
        for purpose in tuple(self._deferred):
            self._set_phase(purpose, BackgroundTaskPhase.IDLE)
        self._deferred.clear()
        self._scheduled_retries.clear()
        logging.info("event=background_task_coordinator_shutdown")

    def _task_succeeded(self, key: str, result: object) -> None:
        task = self._running.get(key)
        if task is None:
            return
        if not isinstance(result, BackgroundTaskResult):
            self._task_failed(key, "Некорректный результат фоновой операции")
            return
        if result.state == BackgroundTaskState.SKIPPED_BUSY:
            self._apply_busy(task.spec, task.callbacks, result)
            return
        self._set_phase(task.spec.purpose, BackgroundTaskPhase.COMPLETED)
        logging.info(
            "event=background_task_completed purpose=%s state=%s",
            task.spec.purpose.value,
            result.state.value,
        )
        self._safe_callback(task.callbacks.on_success, result, task.callbacks.on_failure)

    def _task_failed(self, key: str, details: str) -> None:
        task = self._running.get(key)
        if task is None:
            return
        self._set_phase(task.spec.purpose, BackgroundTaskPhase.FAILED)
        logging.error(
            "event=background_task_failed purpose=%s details=%s",
            task.spec.purpose.value,
            details,
        )
        self._safe_failure(task.callbacks.on_failure, details)

    def _task_finished(self, key: str) -> None:
        task = self._running.pop(key, None)
        if task is None:
            return
        if self.worker_registry is not None and task.worker in self.worker_registry:
            self.worker_registry.remove(task.worker)
        if task.callbacks.on_finished:
            try:
                task.callbacks.on_finished()
            except Exception:
                logging.exception(
                    "Background task finished callback failed: %s",
                    task.spec.purpose.value,
                )
        if task.spec.activity:
            self.resume_deferred(released_activity=task.spec.activity)

    def _apply_busy(
        self,
        spec: BackgroundTaskSpec[Any],
        callbacks: _TaskCallbacks,
        result: BackgroundTaskResult[Any],
    ) -> None:
        if spec.busy_policy == BusyPolicy.DEFER:
            current = self._deferred.get(spec.purpose)
            if current and current.spec.manually_requested and not spec.manually_requested:
                spec = current.spec
                callbacks = current.callbacks
                result = replace(result, manually_requested=True)
            self._deferred[spec.purpose] = _DeferredTask(spec, callbacks, result)
            self._set_phase(spec.purpose, BackgroundTaskPhase.DEFERRED)
        else:
            self._set_phase(spec.purpose, BackgroundTaskPhase.SKIPPED)
        logging.info(
            "event=background_task_busy purpose=%s policy=%s blockers=%s",
            spec.purpose.value,
            spec.busy_policy.value,
            ",".join(item.activity for item in result.blockers) or "unknown",
        )
        self._safe_callback(callbacks.on_busy, result, callbacks.on_failure)

    def _schedule_retry(
        self,
        purpose: BackgroundTaskPurpose,
        item: _DeferredTask,
    ) -> None:
        if purpose in self._scheduled_retries:
            return
        if item.spec.retry_allowed and not item.spec.retry_allowed():
            self.cancel_deferred(purpose)
            return
        self._scheduled_retries.add(purpose)

        def retry() -> None:
            self._scheduled_retries.discard(purpose)
            deferred = self._deferred.pop(purpose, None)
            if deferred is None or self._shutdown:
                return
            if deferred.spec.retry_allowed and not deferred.spec.retry_allowed():
                self._set_phase(purpose, BackgroundTaskPhase.IDLE)
                return
            self.submit(
                deferred.spec,
                on_success=deferred.callbacks.on_success,
                on_busy=deferred.callbacks.on_busy,
                on_failure=deferred.callbacks.on_failure,
                on_finished=deferred.callbacks.on_finished,
            )

        QTimer.singleShot(item.spec.defer_delay_ms, retry)

    def _set_phase(
        self,
        purpose: BackgroundTaskPurpose,
        phase: BackgroundTaskPhase,
    ) -> None:
        self._phases[purpose] = phase
        self.state_changed.emit(purpose.value, phase.value)

    @staticmethod
    def _safe_callback(
        callback: Callable[[BackgroundTaskResult[Any]], None] | None,
        result: BackgroundTaskResult[Any],
        on_failure: Callable[[str], None] | None,
    ) -> None:
        if callback is None:
            return
        try:
            callback(result)
        except Exception:
            details = traceback.format_exc()
            logging.error("Background task callback failed: %s", details)
            BackgroundTaskCoordinator._safe_failure(on_failure, details)

    @staticmethod
    def _safe_failure(
        callback: Callable[[str], None] | None,
        details: str,
    ) -> None:
        if callback is None:
            return
        try:
            callback(details)
        except Exception:
            logging.exception("Background task failure callback failed")
