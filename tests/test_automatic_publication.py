from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

import tutor_assistant.pipeline as pipeline_module
from tutor_assistant.application.publication_queue import (
    PublicationPumpContext,
    PublicationQueueCoordinator,
)
from tutor_assistant.application.recording_stop import (
    RecordingStopSession,
    RecordingStopState,
    StopRecordingUseCase,
)
from tutor_assistant.config import AppConfig, RepositoryConfig
from tutor_assistant.domain import JobStatus, Lesson, LessonProcessingMode, Student
from tutor_assistant.pipeline import LessonPipeline
from tutor_assistant.publication import GitHubRepositoryIdentity, GitRemoteDescriptor
from tutor_assistant.publisher import (
    LessonPublisher,
    PublicationConflictError,
    PublicationPolicy,
    TranscriptPublicationPayload,
)
from tutor_assistant.recording import RecordingResult
from tutor_assistant.transcription import TranscriptionResult


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



def test_stop_to_asr_to_publication_queue_to_verified_git_publication(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repository, remote = make_repository(tmp_path)
    config = AppConfig(workspace=tmp_path / "workspace")
    config.recording.dual_channel_transcription = False
    config.repository = RepositoryConfig(
        students_repo=tmp_path / "public-students-pages",
        remote="origin",
        repository_full_name="ArtemLevin/students-26-27",
        push=True,
    )
    config.automatic_transcript_repository = RepositoryConfig(
        students_repo=repository,
        remote="origin",
        repository_full_name="ArtemLevin/private-students",
        push=True,
    )
    pipeline = LessonPipeline(config)
    lesson = Lesson(
        lesson_id="e2e-auto",
        student=Student(
            id="student",
            full_name="Тестовый ученик",
            repository_folder="students/test_student",
        ),
        subject="mathematics",
        lesson_date=date(2026, 10, 4),
        topic="Полный автоматический pipeline",
    )
    lesson.pipeline.processing_mode = LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB
    pipeline.create(lesson)
    lesson.transition(JobStatus.RECORDING)
    pipeline.save_state(lesson, "status", "error")

    recording_dir = pipeline.lesson_dir(lesson) / "recording"
    recording_dir.mkdir(parents=True)
    mixed = recording_dir / "lesson.wav"
    mixed.write_bytes(b"recorded audio")
    quality = recording_dir / "audio_quality_report.json"
    quality.write_text('{"ready": true, "warnings": []}', encoding="utf-8")
    recording_result = RecordingResult(
        microphone_file=recording_dir / "microphone.wav",
        system_file=recording_dir / "system.wav",
        mixed_file=mixed,
        session_file=recording_dir / "session.json",
        sync_report=recording_dir / "sync_report.json",
        quality_report=quality,
    )

    class Recorder:
        active = True
        quiesced = False

        def stop(self) -> RecordingResult:
            self.active = False
            self.quiesced = True
            return recording_result

    stop_outcome = StopRecordingUseCase(pipeline).stop(
        RecordingStopSession(
            lesson=lesson,
            recorder=Recorder(),
            lease=None,
        )
    )

    assert stop_outcome.state == RecordingStopState.RECORDED
    assert stop_outcome.result is not None
    assert pipeline.store.get(lesson.lesson_id).status == JobStatus.RECORDED

    class E2ETranscriber:
        def transcribe(self, audio: Path, output_dir: Path) -> TranscriptionResult:
            output_dir.mkdir(parents=True, exist_ok=True)
            raw = output_dir / "00_raw_fake.txt"
            timestamped = output_dir / "00_raw_timestamped.txt"
            cleaned = output_dir / "03_content_only_medium.txt"
            segments = output_dir / "00_raw_segments.json"
            signals = output_dir / "important_student_signals.json"
            manifest = output_dir / "manifest.json"
            raw.write_text("raw transcript", encoding="utf-8")
            timestamped.write_text(
                "[00.00 — 01.00] raw transcript",
                encoding="utf-8",
            )
            cleaned.write_text("verified automatic transcript", encoding="utf-8")
            segments.write_text(
                '[{"start": 0, "end": 1, "text": "raw transcript"}]',
                encoding="utf-8",
            )
            signals.write_text("[]", encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "provider": "e2e-fake",
                        "model": "e2e-model",
                        "sources": [{"source_audio": str(audio.resolve())}],
                    }
                ),
                encoding="utf-8",
            )
            return TranscriptionResult(
                output_dir=output_dir,
                raw=raw,
                timestamped=timestamped,
                cleaned=cleaned,
                segments=segments,
                signals=signals,
                manifest=manifest,
            )

    monkeypatch.setattr(pipeline, "transcriber", lambda: E2ETranscriber())
    transcribed = pipeline.transcribe(
        stop_outcome.lesson,
        stop_outcome.result.mixed_file,
    )

    revisions = pipeline.content_service.repository.list_transcript_revisions(
        lesson.lesson_id
    )
    assert transcribed.status == JobStatus.REVIEW_REQUIRED
    assert len(revisions) == 1
    assert revisions[0].created_by == "automatic-transcription"

    stored_jobs = pipeline.store.list_automatic_publication_jobs()
    assert len(stored_jobs) == 1
    coordinator = PublicationQueueCoordinator(pipeline.store)
    assert coordinator.restore_history([transcribed], stored_jobs) == 1
    submission = coordinator.pump(PublicationPumpContext())
    assert submission is not None

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
    publisher = LessonPublisher(
        config.automatic_publication_repository,
        policy=PublicationPolicy(require_private_repository=False),
    )

    def automatic_publisher(publisher_config):
        assert publisher_config is config.automatic_transcript_repository
        return publisher

    monkeypatch.setattr(
        pipeline_module,
        "LessonPublisher",
        automatic_publisher,
    )

    publication = pipeline.publish_automatic_transcript(
        submission.lesson,
        revision_number=submission.revision_number,
        content_sha256=submission.content_sha256,
        repository_path=submission.repository_path,
    )
    coordinator.published(submission.job_id)

    assert publication.remote_verified is True
    assert publication.repository_path == (
        "students/test_student/transcript/04.10.26.txt"
    )
    persisted = pipeline.store.get(lesson.lesson_id)
    assert persisted is not None
    assert persisted.status == JobStatus.PUBLISHED
    persisted_job = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert persisted_job is not None
    assert persisted_job.status == "published"

    published = git(
        tmp_path,
        "--git-dir",
        str(remote),
        "show",
        f"refs/heads/main:{publication.repository_path}",
    )
    assert published == "verified automatic transcript"
    files = git(
        tmp_path,
        "--git-dir",
        str(remote),
        "ls-tree",
        "-r",
        "--name-only",
        "refs/heads/main",
    ).splitlines()
    assert files == ["README.md", publication.repository_path]
