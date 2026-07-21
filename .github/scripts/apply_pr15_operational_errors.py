from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Patch anchor not found in {path}: {old[:120]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


replace(
    "src/tutor_assistant/ui/background.py",
    '''class BackgroundTaskState(StrEnum):
    COMPLETED = "completed"
    NO_CHANGES = "no_changes"
    SKIPPED_BUSY = "skipped_busy"
''',
    '''class BackgroundTaskState(StrEnum):
    COMPLETED = "completed"
    NO_CHANGES = "no_changes"
    SKIPPED_BUSY = "skipped_busy"
    REJECTED = "rejected"
    RETRYABLE_FAILURE = "retryable_failure"
''',
)
replace(
    "src/tutor_assistant/ui/background.py",
    '''    none_is_no_changes: bool = False
    retry_allowed: Callable[[], bool] | None = None
''',
    '''    none_is_no_changes: bool = False
    retry_allowed: Callable[[], bool] | None = None
    handled_exceptions: tuple[type[Exception], ...] = ()
    handled_exception_retryable: bool = False
    handled_exception_message: Callable[[Exception], str] | None = None
''',
)
replace(
    "src/tutor_assistant/ui/background.py",
    '''    @classmethod
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
''',
    '''    @classmethod
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
            state=(
                BackgroundTaskState.RETRYABLE_FAILURE
                if retryable
                else BackgroundTaskState.REJECTED
            ),
            reason=reason,
            manually_requested=manually_requested,
        )
''',
)

replace(
    "src/tutor_assistant/ui/background_tasks.py",
    '''class _TaskCallbacks:
    on_success: Callable[[BackgroundTaskResult[Any]], None] | None = None
    on_busy: Callable[[BackgroundTaskResult[Any]], None] | None = None
    on_failure: Callable[[str], None] | None = None
    on_finished: Callable[[], None] | None = None
''',
    '''class _TaskCallbacks:
    on_success: Callable[[BackgroundTaskResult[Any]], None] | None = None
    on_busy: Callable[[BackgroundTaskResult[Any]], None] | None = None
    on_handled: Callable[[BackgroundTaskResult[Any]], None] | None = None
    on_failure: Callable[[str], None] | None = None
    on_finished: Callable[[], None] | None = None
''',
)
replace(
    "src/tutor_assistant/ui/background_tasks.py",
    '''        on_success: Callable[[BackgroundTaskResult[Any]], None] | None = None,
        on_busy: Callable[[BackgroundTaskResult[Any]], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> bool:
        callbacks = _TaskCallbacks(on_success, on_busy, on_failure, on_finished)
''',
    '''        on_success: Callable[[BackgroundTaskResult[Any]], None] | None = None,
        on_busy: Callable[[BackgroundTaskResult[Any]], None] | None = None,
        on_handled: Callable[[BackgroundTaskResult[Any]], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> bool:
        callbacks = _TaskCallbacks(
            on_success=on_success,
            on_busy=on_busy,
            on_handled=on_handled,
            on_failure=on_failure,
            on_finished=on_finished,
        )
''',
)
replace(
    "src/tutor_assistant/ui/background_tasks.py",
    '''                except ContentBusyError as exc:
                    blockers = exc.blockers or tuple(self.content_service.active_activities())
                    return BackgroundTaskResult[Any].skipped_busy(
                        str(exc),
                        blockers=blockers,
                        manually_requested=spec.manually_requested,
                    )
                if isinstance(payload, BackgroundTaskResult):
''',
    '''                except ContentBusyError as exc:
                    blockers = exc.blockers or tuple(self.content_service.active_activities())
                    return BackgroundTaskResult[Any].skipped_busy(
                        str(exc),
                        blockers=blockers,
                        manually_requested=spec.manually_requested,
                    )
                except Exception as exc:
                    if not spec.handled_exceptions or not isinstance(
                        exc,
                        spec.handled_exceptions,
                    ):
                        raise
                    reason = (
                        spec.handled_exception_message(exc)
                        if spec.handled_exception_message
                        else str(exc)
                    )
                    return BackgroundTaskResult[Any].handled_failure(
                        reason or exc.__class__.__name__,
                        retryable=spec.handled_exception_retryable,
                        manually_requested=spec.manually_requested,
                    )
                if isinstance(payload, BackgroundTaskResult):
''',
)
replace(
    "src/tutor_assistant/ui/background_tasks.py",
    '''        if result.state == BackgroundTaskState.SKIPPED_BUSY:
            self._apply_busy(task.spec, task.callbacks, result)
            return
        self._set_phase(task.spec.purpose, BackgroundTaskPhase.COMPLETED)
''',
    '''        if result.state == BackgroundTaskState.SKIPPED_BUSY:
            self._apply_busy(task.spec, task.callbacks, result)
            return
        if result.state in {
            BackgroundTaskState.REJECTED,
            BackgroundTaskState.RETRYABLE_FAILURE,
        }:
            self._apply_handled(task.spec, task.callbacks, result)
            return
        self._set_phase(task.spec.purpose, BackgroundTaskPhase.COMPLETED)
''',
)
replace(
    "src/tutor_assistant/ui/background_tasks.py",
    '''    def _apply_busy(
        self,
        spec: BackgroundTaskSpec[Any],
        callbacks: _TaskCallbacks,
        result: BackgroundTaskResult[Any],
    ) -> None:
''',
    '''    def _apply_handled(
        self,
        spec: BackgroundTaskSpec[Any],
        callbacks: _TaskCallbacks,
        result: BackgroundTaskResult[Any],
    ) -> None:
        retryable = result.state == BackgroundTaskState.RETRYABLE_FAILURE
        if retryable and spec.busy_policy == BusyPolicy.DEFER:
            current = self._deferred.get(spec.purpose)
            if current and current.spec.manually_requested and not spec.manually_requested:
                spec = current.spec
                callbacks = current.callbacks
                result = replace(result, manually_requested=True)
            self._deferred[spec.purpose] = _DeferredTask(spec, callbacks, result)
            self._set_phase(spec.purpose, BackgroundTaskPhase.DEFERRED)
        else:
            self._set_phase(spec.purpose, BackgroundTaskPhase.SKIPPED)
        logging.warning(
            "event=background_task_handled purpose=%s retryable=%s reason=%s",
            spec.purpose.value,
            retryable,
            result.reason or "unknown",
        )
        self._safe_callback(callbacks.on_handled, result, callbacks.on_failure)

    def _apply_busy(
        self,
        spec: BackgroundTaskSpec[Any],
        callbacks: _TaskCallbacks,
        result: BackgroundTaskResult[Any],
    ) -> None:
''',
)
replace(
    "src/tutor_assistant/ui/background_tasks.py",
    '''                on_success=deferred.callbacks.on_success,
                on_busy=deferred.callbacks.on_busy,
                on_failure=deferred.callbacks.on_failure,
                on_finished=deferred.callbacks.on_finished,
''',
    '''                on_success=deferred.callbacks.on_success,
                on_busy=deferred.callbacks.on_busy,
                on_handled=deferred.callbacks.on_handled,
                on_failure=deferred.callbacks.on_failure,
                on_finished=deferred.callbacks.on_finished,
''',
)

replace(
    "src/tutor_assistant/config.py",
    '''    poll_seconds: int = 60
    reservation_timeout_minutes: int = Field(default=30, ge=5, le=1440)
''',
    '''    poll_seconds: int = 60
    reservation_timeout_minutes: int = Field(default=30, ge=5, le=1440)
    remote_fetch_attempts: int = Field(default=3, ge=1, le=10)
    remote_fetch_backoff_seconds: float = Field(default=0.5, ge=0, le=30)
''',
)
replace(
    "config/app.example.yaml",
    '''  poll_seconds: 60
''',
    '''  poll_seconds: 60
  reservation_timeout_minutes: 30
  remote_fetch_attempts: 3
  remote_fetch_backoff_seconds: 0.5
''',
)

replace(
    "src/tutor_assistant/latex/remote.py",
    '''import json
import shutil
import tempfile
''',
    '''import json
import logging
import shutil
import tempfile
''',
)
replace(
    "src/tutor_assistant/latex/remote.py",
    '''from pathlib import Path
''',
    '''from pathlib import Path
from time import sleep
''',
)
replace(
    "src/tutor_assistant/latex/remote.py",
    '''LATEX_MONITOR_STATUSES = {
    JobStatus.PUBLISHED,
    JobStatus.GENERATED_TEX,
    JobStatus.COMPILING_PDF,
    JobStatus.COMPILE_FAILED,
    JobStatus.PDF_REVIEW_REQUIRED,
}


def _is_missing_remote_ref(error: GitError) -> bool:
''',
    '''LATEX_MONITOR_STATUSES = {
    JobStatus.PUBLISHED,
    JobStatus.GENERATED_TEX,
    JobStatus.COMPILING_PDF,
    JobStatus.COMPILE_FAILED,
    JobStatus.PDF_REVIEW_REQUIRED,
}


class RemoteRepositoryUnavailable(GitError):
    """A transient Git transport failure that can be retried safely."""


_TRANSIENT_GIT_MARKERS = (
    "empty reply from server",
    "could not resolve host",
    "failed to connect",
    "connection timed out",
    "operation timed out",
    "connection reset",
    "recv failure",
    "send failure",
    "remote end hung up unexpectedly",
    "http/2 stream",
    "http2 framing layer",
    "network is unreachable",
    "proxy connect aborted",
    "tls connection was non-properly terminated",
)


def _is_transient_git_error(error: GitError) -> bool:
    message = str(error).casefold()
    return any(marker in message for marker in _TRANSIENT_GIT_MARKERS)


def _is_missing_remote_ref(error: GitError) -> bool:
''',
)
replace(
    "src/tutor_assistant/latex/remote.py",
    '''        self.latex = latex
        self.repo = repository.students_repo.resolve()

    def is_candidate(self, lesson: Lesson, *, force: bool = False) -> bool:
''',
    '''        self.latex = latex
        self.repo = repository.students_repo.resolve()

    def _fetch_remote_branch(self, branch: str) -> bool:
        attempts = self.latex.remote_fetch_attempts
        for attempt in range(1, attempts + 1):
            try:
                run_git(self.repo, "fetch", self.repository.remote, branch)
                return True
            except GitError as exc:
                if _is_missing_remote_ref(exc):
                    return False
                if not _is_transient_git_error(exc):
                    raise
                if attempt >= attempts:
                    raise RemoteRepositoryUnavailable(
                        "GitHub временно не отвечает; проверка LaTeX будет повторена автоматически"
                    ) from exc
                delay = self.latex.remote_fetch_backoff_seconds * (2 ** (attempt - 1))
                logging.warning(
                    "event=remote_latex_fetch_retry branch=%s attempt=%s/%s delay=%.2f details=%s",
                    branch,
                    attempt,
                    attempts,
                    delay,
                    str(exc),
                )
                if delay:
                    sleep(delay)
        raise RuntimeError("unreachable")

    def is_candidate(self, lesson: Lesson, *, force: bool = False) -> bool:
''',
)
replace(
    "src/tutor_assistant/latex/remote.py",
    '''        remote_ref = f"{self.repository.remote}/{branch}"
        try:
            run_git(self.repo, "fetch", self.repository.remote, branch)
        except GitError as exc:
            if _is_missing_remote_ref(exc):
                return None
            raise
        remote_head = run_git(self.repo, "rev-parse", remote_ref)
''',
    '''        remote_ref = f"{self.repository.remote}/{branch}"
        if not self._fetch_remote_branch(branch):
            return None
        remote_head = run_git(self.repo, "rev-parse", remote_ref)
''',
)

replace(
    "src/tutor_assistant/latex/__init__.py",
    '''    RemoteCompilationResult,
    RemoteLatexService,
    RemoteTexProbe,
''',
    '''    RemoteCompilationResult,
    RemoteLatexService,
    RemoteRepositoryUnavailable,
    RemoteTexProbe,
''',
)
replace(
    "src/tutor_assistant/latex/__init__.py",
    '''    "RemoteCompilationResult",
    "RemoteLatexService",
    "RemoteTexProbe",
''',
    '''    "RemoteCompilationResult",
    "RemoteLatexService",
    "RemoteRepositoryUnavailable",
    "RemoteTexProbe",
''',
)

replace(
    "src/tutor_assistant/ui/concurrent_app.py",
    '''from ..domain import JobStatus, Lesson
''',
    '''from ..content import ContentConflictError, ContentNotFoundError
from ..domain import JobStatus, Lesson
from ..latex import RemoteRepositoryUnavailable
''',
)
replace(
    "src/tutor_assistant/ui/concurrent_app.py",
    '''                busy_policy=BusyPolicy.FAIL,
                allow_parallel=True,
            ),
            on_success=lambda result: succeeded(result.payload),
            on_busy=lambda result: failed(result.reason or "Хранилище временно занято"),
            on_failure=failed,
''',
    '''                busy_policy=BusyPolicy.FAIL,
                allow_parallel=True,
                handled_exceptions=(ContentConflictError, ContentNotFoundError),
            ),
            on_success=lambda result: succeeded(result.payload),
            on_busy=lambda result: failed(result.reason or "Хранилище временно занято"),
            on_handled=lambda result: failed(result.reason or "Операция недоступна"),
            on_failure=failed,
''',
)
replace(
    "src/tutor_assistant/ui/concurrent_app.py",
    '''                none_is_no_changes=True,
                retry_allowed=lambda: bool(manually_requested or self.auto_latex.isChecked()),
            ),
            on_success=self._remote_monitor_ready,
            on_busy=self._remote_monitor_busy,
            on_failure=lambda details: self._operation_failed("latex-monitor", details),
''',
    '''                none_is_no_changes=True,
                retry_allowed=lambda: bool(manually_requested or self.auto_latex.isChecked()),
                handled_exceptions=(RemoteRepositoryUnavailable,),
                handled_exception_retryable=True,
            ),
            on_success=self._remote_monitor_ready,
            on_busy=self._remote_monitor_busy,
            on_handled=self._remote_monitor_unavailable,
            on_failure=lambda details: self._operation_failed("latex-monitor", details),
''',
)
replace(
    "src/tutor_assistant/ui/concurrent_app.py",
    '''    def _remote_monitor_busy(
        self,
        result: BackgroundTaskResult[object],
    ) -> None:
''',
    '''    def _remote_monitor_unavailable(
        self,
        result: BackgroundTaskResult[object],
    ) -> None:
        message = result.reason or "GitHub временно недоступен"
        self.latex_monitor_status.setText(
            "GitHub временно недоступен; повторю проверку автоматически"
        )
        self._set_status(
            "Проверка LaTeX отложена из-за временной сетевой ошибки",
            "warning",
        )
        if result.manually_requested:
            QMessageBox.warning(self, "Проверка LaTeX", message)

    def _remote_monitor_busy(
        self,
        result: BackgroundTaskResult[object],
    ) -> None:
''',
)

replace(
    "src/tutor_assistant/ui/student_content.py",
    '''        lesson = content.lesson
        answer = QMessageBox.question(
''',
    '''        lesson = content.lesson
        if lesson.status in {JobStatus.RECORDING, JobStatus.TRANSCRIBING}:
            message = "Нельзя удалить занятие во время записи или транскрибации"
            self.status_changed.emit(message, "warning")
            QMessageBox.warning(self, "Удаление недоступно", message)
            return
        answer = QMessageBox.question(
''',
)
replace(
    "src/tutor_assistant/ui/student_content.py",
    '''        message = self._operation_message(details, "Не удалось переместить занятие в корзину")
        self.status_changed.emit(message, "error")
        QMessageBox.warning(self, "Корзина", message)
''',
    '''        message = self._operation_message(details, "Не удалось переместить занятие в корзину")
        expected = message.startswith("Нельзя удалить")
        self.status_changed.emit(message, "warning" if expected else "error")
        QMessageBox.warning(
            self,
            "Удаление недоступно" if expected else "Корзина",
            message,
        )
''',
)

replace(
    "tests/test_background_task_coordinator.py",
    '''    BackgroundTaskPhase,
    BackgroundTaskPurpose,
    BackgroundTaskSpec,
''',
    '''    BackgroundTaskPhase,
    BackgroundTaskPurpose,
    BackgroundTaskSpec,
    BackgroundTaskState,
''',
)
append = '''\n\ndef test_handled_domain_error_uses_non_failure_channel(\n    tmp_path: Path,\n    application: QApplication,\n) -> None:\n    service = StudentContentService(tmp_path / "handled" / "data")\n    coordinator = BackgroundTaskCoordinator(service)\n    handled = []\n    failures: list[str] = []\n\n    def reject() -> None:\n        raise ValueError("Нельзя удалить активное занятие")\n\n    assert coordinator.submit(\n        BackgroundTaskSpec(\n            purpose=BackgroundTaskPurpose.CONTENT_BROWSER,\n            operation=reject,\n            handled_exceptions=(ValueError,),\n        ),\n        on_handled=handled.append,\n        on_failure=failures.append,\n    )\n    wait_until(application, lambda: coordinator.running_count() == 0)\n\n    assert handled and handled[0].state == BackgroundTaskState.REJECTED\n    assert "Нельзя удалить" in (handled[0].reason or "")\n    assert failures == []\n    assert coordinator.phase(BackgroundTaskPurpose.CONTENT_BROWSER) == (\n        BackgroundTaskPhase.SKIPPED\n    )\n\n\ndef test_retryable_handled_error_is_deferred_without_failure_traceback(\n    tmp_path: Path,\n    application: QApplication,\n) -> None:\n    service = StudentContentService(tmp_path / "retryable" / "data")\n    coordinator = BackgroundTaskCoordinator(service)\n    handled = []\n    failures: list[str] = []\n\n    def unavailable() -> None:\n        raise ConnectionError("GitHub временно недоступен")\n\n    assert coordinator.submit(\n        BackgroundTaskSpec(\n            purpose=BackgroundTaskPurpose.LATEX_MONITOR,\n            operation=unavailable,\n            busy_policy=BusyPolicy.DEFER,\n            handled_exceptions=(ConnectionError,),\n            handled_exception_retryable=True,\n        ),\n        on_handled=handled.append,\n        on_failure=failures.append,\n    )\n    wait_until(application, lambda: coordinator.running_count() == 0)\n\n    assert handled and handled[0].state == BackgroundTaskState.RETRYABLE_FAILURE\n    assert failures == []\n    assert coordinator.phase(BackgroundTaskPurpose.LATEX_MONITOR) == (\n        BackgroundTaskPhase.DEFERRED\n    )\n    assert coordinator.has_pending()\n    coordinator.begin_shutdown()\n'''
file = Path("tests/test_background_task_coordinator.py")
file.write_text(file.read_text(encoding="utf-8") + append, encoding="utf-8")

replace(
    "tests/test_latex_remote.py",
    '''from tutor_assistant.latex.remote import RemoteLatexService
''',
    '''from tutor_assistant.latex.remote import RemoteLatexService, RemoteRepositoryUnavailable
''',
)
replace(
    "tests/test_latex_remote.py",
    '''def service(tmp_path: Path) -> RemoteLatexService:
    repository = tmp_path / "students"
    repository.mkdir()
    return RemoteLatexService(
        RepositoryConfig(students_repo=repository),
        LatexConfig(),
    )
''',
    '''def service(
    tmp_path: Path,
    *,
    attempts: int = 1,
    backoff: float = 0,
) -> RemoteLatexService:
    repository = tmp_path / "students"
    repository.mkdir()
    return RemoteLatexService(
        RepositoryConfig(students_repo=repository),
        LatexConfig(
            remote_fetch_attempts=attempts,
            remote_fetch_backoff_seconds=backoff,
        ),
    )
''',
)
replace(
    "tests/test_latex_remote.py",
    '''def test_remote_transport_error_is_not_hidden(tmp_path: Path, monkeypatch) -> None:
    def network_failure(_repo: Path, *_args: str) -> str:
        raise GitError("fatal: unable to access remote: connection timed out")

    monkeypatch.setattr(remote_module, "run_git", network_failure)

    with pytest.raises(GitError, match="connection timed out"):
        service(tmp_path).is_ready(published_lesson())
''',
    '''def test_transient_remote_transport_error_becomes_retryable_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def network_failure(_repo: Path, *_args: str) -> str:
        raise GitError("fatal: unable to access remote: Empty reply from server")

    monkeypatch.setattr(remote_module, "run_git", network_failure)

    with pytest.raises(RemoteRepositoryUnavailable, match="повторена автоматически"):
        service(tmp_path).is_ready(published_lesson())


def test_non_transient_git_error_is_not_hidden(tmp_path: Path, monkeypatch) -> None:
    def authentication_failure(_repo: Path, *_args: str) -> str:
        raise GitError("fatal: Authentication failed")

    monkeypatch.setattr(remote_module, "run_git", authentication_failure)

    with pytest.raises(GitError, match="Authentication failed"):
        service(tmp_path).is_ready(published_lesson())


def test_transient_fetch_is_retried_before_probe_continues(tmp_path: Path, monkeypatch) -> None:
    fetch_calls = 0

    def flaky_git(_repo: Path, *args: str) -> str:
        nonlocal fetch_calls
        if args[0] == "fetch":
            fetch_calls += 1
            if fetch_calls == 1:
                raise GitError("fatal: Empty reply from server")
            return ""
        if args[:2] == ("rev-parse", "origin/lesson/student-20260718-remote"):
            return "remote-head"
        if args[0] == "ls-tree":
            return "students/student/lesson/handbook/lesson.tex"
        if args[0] == "rev-parse":
            return "tex-blob"
        raise AssertionError(args)

    monkeypatch.setattr(remote_module, "run_git", flaky_git)
    monkeypatch.setattr(remote_module, "sleep", lambda _delay: None)

    probe = service(tmp_path, attempts=3).probe_lesson(published_lesson())

    assert fetch_calls == 2
    assert probe is not None
    assert probe.remote_head == "remote-head"
    assert probe.blob_sha == "tex-blob"
''',
)

replace(
    "tests/test_student_content_gui.py",
    '''from tutor_assistant.domain import Lesson, Student  # noqa: E402
''',
    '''from tutor_assistant.domain import JobStatus, Lesson, Student  # noqa: E402
''',
)
append = '''\n\ndef test_active_lesson_delete_is_a_warning_without_background_failure(\n    tmp_path: Path,\n    application: QApplication,\n    monkeypatch,\n) -> None:\n    page, service = make_page(tmp_path)\n    content = service.get_lesson("gui-lesson")\n    lesson = content.lesson\n    lesson.transition(JobStatus.RECORDING, force=True)\n    service.repository.upsert_lesson(lesson)\n    page.refresh()\n    page.table.selectRow(0)\n    application.processEvents()\n\n    warnings: list[tuple[str, str]] = []\n    monkeypatch.setattr(\n        QMessageBox,\n        "warning",\n        lambda _parent, title, message, *_args, **_kwargs: warnings.append(\n            (title, message)\n        ),\n    )\n    monkeypatch.setattr(\n        QMessageBox,\n        "question",\n        lambda *_args, **_kwargs: (_ for _ in ()).throw(\n            AssertionError("confirmation must not open for active lesson")\n        ),\n    )\n\n    page.delete_selected_lesson()\n\n    assert warnings == [\n        (\n            "Удаление недоступно",\n            "Нельзя удалить занятие во время записи или транскрибации",\n        )\n    ]\n    assert service.list_lessons().total == 1\n    page.close()\n'''
file = Path("tests/test_student_content_gui.py")
file.write_text(file.read_text(encoding="utf-8") + append, encoding="utf-8")
