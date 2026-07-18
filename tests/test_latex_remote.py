from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import tutor_assistant.latex.remote as remote_module
from tutor_assistant.config import LatexConfig, RepositoryConfig
from tutor_assistant.domain import JobStatus, Lesson, PublicationInfo, Student
from tutor_assistant.latex.remote import RemoteLatexService
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


def service(tmp_path: Path) -> RemoteLatexService:
    repository = tmp_path / "students"
    repository.mkdir()
    return RemoteLatexService(
        RepositoryConfig(students_repo=repository),
        LatexConfig(),
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


def test_remote_transport_error_is_not_hidden(tmp_path: Path, monkeypatch) -> None:
    def network_failure(_repo: Path, *_args: str) -> str:
        raise GitError("fatal: unable to access remote: connection timed out")

    monkeypatch.setattr(remote_module, "run_git", network_failure)

    with pytest.raises(GitError, match="connection timed out"):
        service(tmp_path).is_ready(published_lesson())


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
