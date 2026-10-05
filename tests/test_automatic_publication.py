from __future__ import annotations

import hashlib
import subprocess
from datetime import date
from pathlib import Path

import pytest

from tutor_assistant.config import RepositoryConfig
from tutor_assistant.domain import JobStatus, Lesson, LessonProcessingMode, Student
from tutor_assistant.publication import GitHubRepositoryIdentity, GitRemoteDescriptor
from tutor_assistant.publisher import (
    LessonPublisher,
    PublicationConflictError,
    PublicationPolicy,
    TranscriptPublicationPayload,
)


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


def make_lesson(identifier: str) -> Lesson:
    lesson = Lesson(
        lesson_id=identifier,
        student=Student(
            id="student",
            full_name="Тестовый ученик",
            repository_folder="students/test_student",
        ),
        subject="mathematics",
        lesson_date=date(2026, 10, 4),
        topic="Автоматическая публикация",
        status=JobStatus.REVIEW_REQUIRED,
    )
    lesson.pipeline.processing_mode = LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB
    return lesson


def make_payload(lesson: Lesson, content: str) -> TranscriptPublicationPayload:
    return TranscriptPublicationPayload(
        lesson_id=lesson.lesson_id,
        repository_path="students/test_student/transcript/04.10.26.txt",
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        revision_number=1,
    )


def make_publisher(
    monkeypatch,
    repository: Path,
) -> LessonPublisher:
    descriptor = GitRemoteDescriptor(
        remote_name="origin",
        identity=GitHubRepositoryIdentity(
            host="github.com",
            owner="ArtemLevin",
            repository="private-students",
        ),
        url_sha256="a" * 64,
    )
    monkeypatch.setattr(
        LessonPublisher,
        "_descriptor",
        lambda _self, _repo: descriptor,
    )
    config = RepositoryConfig(
        students_repo=repository,
        remote="origin",
        repository_full_name="ArtemLevin/private-students",
        push=True,
    )
    return LessonPublisher(
        config,
        policy=PublicationPolicy(require_private_repository=False),
    )


def test_automatic_target_absent_publishes_exact_single_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repository, remote = make_repository(tmp_path)
    publisher = make_publisher(monkeypatch, repository)
    lesson = make_lesson("auto-first")
    lesson_dir = tmp_path / "workspace" / "lessons" / lesson.lesson_id
    lesson_dir.mkdir(parents=True)
    payload = make_payload(lesson, "automatic transcript\n")

    result = publisher.publish_payload(
        lesson,
        lesson_dir,
        payload,
        reject_existing_mismatch=True,
    )

    assert result.remote_verified is True
    assert result.idempotent is False
    assert result.repository_path == payload.repository_path
    assert lesson.status == JobStatus.PUBLISHED
    published = git(
        tmp_path,
        "--git-dir",
        str(remote),
        "show",
        f"refs/heads/main:{payload.repository_path}",
    )
    assert published == "automatic transcript"
    files = git(
        tmp_path,
        "--git-dir",
        str(remote),
        "ls-tree",
        "-r",
        "--name-only",
        "refs/heads/main",
    ).splitlines()
    assert files == ["README.md", payload.repository_path]


def test_automatic_target_same_sha_is_idempotent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repository, _remote = make_repository(tmp_path)
    publisher = make_publisher(monkeypatch, repository)
    lesson = make_lesson("auto-same")
    lesson_dir = tmp_path / "workspace" / "lessons" / lesson.lesson_id
    lesson_dir.mkdir(parents=True)
    payload = make_payload(lesson, "same transcript\n")

    first = publisher.publish_payload(
        lesson,
        lesson_dir,
        payload,
        reject_existing_mismatch=True,
    )
    repeated = publisher.publish_payload(
        lesson,
        lesson_dir,
        payload,
        reject_existing_mismatch=True,
    )

    assert first.idempotent is False
    assert repeated.idempotent is True
    assert repeated.commit == first.commit
    assert lesson.status == JobStatus.PUBLISHED


def test_automatic_target_different_sha_conflicts_without_overwrite(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repository, remote = make_repository(tmp_path)
    publisher = make_publisher(monkeypatch, repository)

    first = make_lesson("auto-original")
    first_dir = tmp_path / "workspace" / "lessons" / first.lesson_id
    first_dir.mkdir(parents=True)
    original = make_payload(first, "original transcript\n")
    publisher.publish_payload(
        first,
        first_dir,
        original,
        reject_existing_mismatch=True,
    )

    second = make_lesson("auto-collision")
    second_dir = tmp_path / "workspace" / "lessons" / second.lesson_id
    second_dir.mkdir(parents=True)
    conflicting = make_payload(second, "different transcript\n")

    with pytest.raises(PublicationConflictError, match="другим содержимым"):
        publisher.publish_payload(
            second,
            second_dir,
            conflicting,
            reject_existing_mismatch=True,
        )

    published = git(
        tmp_path,
        "--git-dir",
        str(remote),
        "show",
        f"refs/heads/main:{original.repository_path}",
    )
    assert published == "original transcript"
    assert second.status == JobStatus.REVIEW_REQUIRED
