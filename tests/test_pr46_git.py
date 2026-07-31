from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from tutor_assistant.config import RepositoryConfig
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.publication import GitHubRepositoryIdentity, GitRemoteDescriptor
from tutor_assistant.publisher import LessonPublisher, PublicationPolicy


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def make_repository(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "students.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    repository = tmp_path / "students"
    git(tmp_path, "clone", str(remote), str(repository))
    git(repository, "config", "user.name", "Tutor Assistant Test")
    git(repository, "config", "user.email", "test@example.invalid")
    (repository / "README.md").write_text("students\n", encoding="utf-8")
    git(repository, "add", "README.md")
    git(repository, "commit", "-m", "Initialize students repository")
    git(repository, "branch", "-M", "main")
    git(repository, "push", "-u", "origin", "main")
    return repository, remote


def make_lesson(workspace: Path) -> tuple[Lesson, Path]:
    lesson = Lesson(
        lesson_id="lesson-publication-integration",
        student=Student(
            id="student",
            full_name="Тестовый ученик",
            repository_folder="students/test_student",
        ),
        subject="mathematics",
        lesson_date=date(2026, 7, 31),
        topic="Логарифмические неравенства",
        status=JobStatus.READY,
    )
    lesson_dir = workspace / "lessons" / lesson.lesson_id
    transcript = lesson_dir / "transcript" / "transcript_verified.txt"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("[П] Проверяем публикацию\n", encoding="utf-8")
    lesson.artifacts.verified_transcript = str(transcript)
    return lesson, lesson_dir


def test_verified_push_and_idempotent_retry(monkeypatch, tmp_path: Path) -> None:
    repository, remote = make_repository(tmp_path)
    workspace = tmp_path / "workspace"
    lesson, lesson_dir = make_lesson(workspace)
    config = RepositoryConfig(
        students_repo=repository,
        remote="origin",
        repository_full_name="ArtemLevin/private-students",
        push=True,
    )
    descriptor = GitRemoteDescriptor(
        remote_name="origin",
        identity=GitHubRepositoryIdentity(
            host="github.com",
            owner="ArtemLevin",
            repository="private-students",
        ),
        url_sha256="a" * 64,
    )
    monkeypatch.setattr(LessonPublisher, "_descriptor", lambda _self, _repo: descriptor)
    publisher = LessonPublisher(
        config,
        policy=PublicationPolicy(require_private_repository=False),
    )

    result = publisher.publish(lesson, lesson_dir)

    assert result.remote_verified is True
    assert result.idempotent is False
    assert lesson.status == JobStatus.PUBLISHED
    published = git(
        tmp_path,
        "--git-dir",
        str(remote),
        "show",
        f"refs/heads/main:{result.repository_path}",
    )
    assert published == "[П] Проверяем публикацию"

    retry = lesson.model_copy(deep=True)
    retry.status = JobStatus.READY
    repeated = publisher.publish(retry, lesson_dir)

    assert repeated.remote_verified is True
    assert repeated.idempotent is True
    assert repeated.commit == result.commit
    assert retry.status == JobStatus.PUBLISHED
