from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import tutor_assistant.latex.remote as remote_module
from tutor_assistant.config import LatexConfig, RepositoryConfig
from tutor_assistant.domain import JobStatus, Lesson, PublicationInfo, Student
from tutor_assistant.latex.remote import RemoteLatexService, RemoteRepositoryUnavailable
from tutor_assistant.publisher import GitError


def published_lesson(*, status: JobStatus = JobStatus.PUBLISHED) -> Lesson:
    return Lesson(
        lesson_id="remote-latex",
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 18),
        topic="Удалённая ветка",
        status=status,
        publication=PublicationInfo(
            branch="lesson/student-20260718-remote",
            repository_path="students/student/lesson",
            commit="abc123",
        ),
    )


def service(
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


def test_deleted_remote_branch_is_an_expected_not_ready_state(tmp_path: Path, monkeypatch) -> None:
    def missing_branch(_repo: Path, *args: str) -> str:
        assert args[:2] == ("fetch", "origin")
        raise GitError("fatal: couldn't find remote ref lesson/student-20260718-remote")

    monkeypatch.setattr(remote_module, "run_git", missing_branch)
    monitor = service(tmp_path)
    lesson = published_lesson()

    assert monitor.find_tex(lesson) is None
    assert not monitor.is_ready(lesson)


def test_transient_remote_transport_error_becomes_retryable_unavailable(
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


def test_completed_lesson_does_not_poll_its_old_publication_branch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        remote_module,
        "run_git",
        lambda *_args: (_ for _ in ()).throw(AssertionError("completed lesson must not fetch")),
    )

    assert not service(tmp_path).is_ready(published_lesson(status=JobStatus.COMPLETED))
