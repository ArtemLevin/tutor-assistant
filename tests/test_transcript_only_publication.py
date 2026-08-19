from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from tutor_assistant.config import RepositoryConfig
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.publisher import (
    ApprovedTranscriptPayload,
    GitError,
    LessonPublisher,
    PublicationPolicy,
    _assert_transcript_only_egress,
    publication_payload_files,
    publication_repository_path,
)


def make_lesson(tmp_path: Path, *, status: JobStatus = JobStatus.READY) -> Lesson:
    transcript = tmp_path / "transcript_verified.txt"
    transcript.write_text("[П] Подтверждённый текст\n", encoding="utf-8")
    lesson = Lesson(
        lesson_id="1234567890abcdef1234567890abcdef",
        student=Student(
            id="student",
            full_name="Тестовый ученик",
            repository_folder="students/test_student",
        ),
        subject="mathematics",
        lesson_date=date(2026, 7, 30),
        topic="Логарифмические неравенства",
        status=status,
    )
    lesson.artifacts.verified_transcript = str(transcript)
    lesson.artifacts.cleaned_transcript = str(tmp_path / "transcript_cleaned.txt")
    lesson.artifacts.timestamped_transcript = str(tmp_path / "transcript_timestamped.txt")
    lesson.artifacts.segments_json = str(tmp_path / "segments.json")
    lesson.artifacts.student_signals = str(tmp_path / "important_student_signals.json")
    lesson.artifacts.transcription_manifest = str(tmp_path / "transcription_manifest.json")
    lesson.artifacts.teacher_transcript = str(tmp_path / "teacher_transcript.txt")
    lesson.artifacts.student_transcript = str(tmp_path / "student_transcript.txt")
    return lesson


def test_policy_is_fixed_to_transcript_on_main() -> None:
    policy = PublicationPolicy()

    assert policy.allowed_filename == "transcript.txt"
    assert policy.target_branch == "main"
    assert policy.require_private_repository is True


def test_payload_ignores_every_local_derivative(tmp_path: Path) -> None:
    lesson = make_lesson(tmp_path)
    expected = (
        f"students/test_student/lessons/{lesson.lesson_slug}__"
        f"{lesson.lesson_id[:8]}/transcript.txt"
    )

    assert publication_repository_path(lesson).as_posix() == expected
    assert publication_payload_files(lesson) == (expected,)
    assert len(publication_payload_files(lesson)) == 1


def test_equal_date_and_topic_still_produce_unique_paths(tmp_path: Path) -> None:
    first = make_lesson(tmp_path)
    second = first.model_copy(deep=True)
    second.lesson_id = "abcdef1234567890abcdef1234567890"

    assert publication_repository_path(first) != publication_repository_path(second)


def test_legacy_non_uuid_path_is_stable(tmp_path: Path) -> None:
    lesson = make_lesson(tmp_path)
    lesson.lesson_id = "legacy-publication"

    assert publication_repository_path(lesson).as_posix() == (
        f"students/test_student/lessons/{lesson.lesson_slug}/transcript.txt"
    )


@pytest.mark.parametrize(
    "status",
    [
        JobStatus.DRAFT,
        JobStatus.RECORDING,
        JobStatus.RECORDED,
        JobStatus.TRANSCRIBING,
        JobStatus.REVIEW_REQUIRED,
        JobStatus.PUBLISHED,
        JobStatus.FAILED,
    ],
)
def test_publication_requires_fresh_user_approval(tmp_path: Path, status: JobStatus) -> None:
    lesson = make_lesson(tmp_path, status=status)
    config = RepositoryConfig(students_repo=tmp_path / "missing-repository")

    with pytest.raises(GitError, match="только после подтверждения"):
        LessonPublisher(config).publish(lesson, tmp_path)


def test_publication_requires_verified_transcript_path(tmp_path: Path) -> None:
    lesson = make_lesson(tmp_path)
    lesson.artifacts.verified_transcript = None
    config = RepositoryConfig(students_repo=tmp_path / "missing-repository")

    with pytest.raises(GitError, match="транскрипт отсутствует"):
        LessonPublisher(config).publish(lesson, tmp_path)


def test_publication_rejects_missing_verified_file(tmp_path: Path) -> None:
    lesson = make_lesson(tmp_path)
    lesson.artifacts.verified_transcript = str(tmp_path / "deleted.txt")
    config = RepositoryConfig(students_repo=tmp_path / "missing-repository")

    with pytest.raises(GitError, match="не найден"):
        LessonPublisher(config).publish(lesson, tmp_path)


def test_publication_rejects_non_utf8_transcript(tmp_path: Path) -> None:
    lesson = make_lesson(tmp_path)
    transcript = tmp_path / "binary.txt"
    transcript.write_bytes(b"\xff\xfe\x00")
    lesson.artifacts.verified_transcript = str(transcript)
    config = RepositoryConfig(students_repo=tmp_path / "missing-repository")

    with pytest.raises(GitError, match="UTF-8"):
        LessonPublisher(config).publish(lesson, tmp_path)


def test_publication_rejects_projection_changed_after_sqlite_approval(tmp_path: Path) -> None:
    lesson = make_lesson(tmp_path)
    approved_text = "[П] Подтверждённый текст\n"
    approved = ApprovedTranscriptPayload(
        content=approved_text,
        content_sha256=hashlib.sha256(approved_text.encode("utf-8")).hexdigest(),
        revision_number=7,
    )
    Path(lesson.artifacts.verified_transcript).write_text(
        "[П] Уже другой текст\n",
        encoding="utf-8",
    )
    config = RepositoryConfig(students_repo=tmp_path / "missing-repository")

    with pytest.raises(GitError, match="отличается от подтверждённой SQLite revision"):
        LessonPublisher(config).publish(lesson, tmp_path, approved=approved)


def test_publication_rejects_corrupted_authoritative_revision_hash(tmp_path: Path) -> None:
    lesson = make_lesson(tmp_path)
    approved = ApprovedTranscriptPayload(
        content="[П] Подтверждённый текст\n",
        content_sha256="0" * 64,
        revision_number=7,
    )
    config = RepositoryConfig(students_repo=tmp_path / "missing-repository")

    with pytest.raises(GitError, match="некорректный SHA-256"):
        LessonPublisher(config).publish(lesson, tmp_path, approved=approved)


def test_production_publication_rejects_push_disabled(tmp_path: Path) -> None:
    lesson = make_lesson(tmp_path)
    config = RepositoryConfig(
        students_repo=tmp_path / "missing-repository",
        push=False,
    )

    with pytest.raises(GitError, match="repository.push=false"):
        LessonPublisher(config).publish(lesson, tmp_path)
    assert lesson.status == JobStatus.READY


@pytest.mark.parametrize(
    "paths",
    [
        ("lesson.json",),
        ("job.status.json",),
        ("source/segments.json",),
        ("recording/lesson.m4a",),
        ("students/test/transcript.txt", "students/test/lesson.json"),
    ],
)
def test_egress_guard_blocks_every_additional_path(paths: tuple[str, ...]) -> None:
    expected = "students/test/lessons/lesson/transcript.txt"

    with pytest.raises(GitError, match="Публикация заблокирована"):
        _assert_transcript_only_egress(paths, expected)


def test_egress_guard_accepts_only_expected_transcript() -> None:
    expected = "students/test/lessons/lesson/transcript.txt"

    _assert_transcript_only_egress((expected,), expected)
    _assert_transcript_only_egress((), expected)
