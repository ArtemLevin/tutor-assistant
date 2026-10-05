import hashlib
import json
from datetime import date

import pytest

import tutor_assistant.pipeline as pipeline_module
from tutor_assistant.config import AppConfig, RepositoryConfig
from tutor_assistant.domain import JobStatus, Lesson, LessonProcessingMode, Student
from tutor_assistant.pipeline import LessonPipeline
from tutor_assistant.publisher import PublicationResult
from tutor_assistant.transcription import EmptyTranscriptionError, TranscriptionResult


class FailingTranscriber:
    def transcribe(self, _audio, _output_dir):
        raise RuntimeError("model failure")


class DurableTranscriber:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio, output_dir):
        self.calls += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        raw = output_dir / "00_raw_fake.txt"
        timestamped = output_dir / "00_raw_timestamped.txt"
        cleaned = output_dir / "03_content_only_medium.txt"
        segments = output_dir / "00_raw_segments.json"
        signals = output_dir / "important_student_signals.json"
        manifest = output_dir / "manifest.json"
        raw.write_text("raw transcript", encoding="utf-8")
        timestamped.write_text("[00.00 — 01.00] raw transcript", encoding="utf-8")
        cleaned.write_text("clean transcript", encoding="utf-8")
        segments.write_text('[{"start": 0, "end": 1, "text": "raw transcript"}]', encoding="utf-8")
        signals.write_text("[]", encoding="utf-8")
        manifest.write_text(
            json.dumps(
                {
                    "provider": "fake",
                    "model": "fake-model",
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


class EmptyTranscriber:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio, output_dir):
        self.calls += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        raw = output_dir / "00_raw_fake.txt"
        timestamped = output_dir / "00_raw_timestamped.txt"
        cleaned = output_dir / "03_content_only_medium.txt"
        segments = output_dir / "00_raw_segments.json"
        signals = output_dir / "important_student_signals.json"
        manifest = output_dir / "manifest.json"
        raw.write_text("", encoding="utf-8")
        timestamped.write_text("", encoding="utf-8")
        cleaned.write_text("", encoding="utf-8")
        segments.write_text("[]", encoding="utf-8")
        signals.write_text("[]", encoding="utf-8")
        manifest.write_text(
            json.dumps(
                {
                    "provider": "fake",
                    "model": "fake-model",
                    "segment_count": 0,
                    "sources": [
                        {
                            "source_audio": str(audio.resolve()),
                            "duration_seconds": 180.0,
                        }
                    ],
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


def _recorded_lesson(
    pipeline: LessonPipeline,
    *,
    processing_mode: LessonProcessingMode = LessonProcessingMode.MANUAL,
) -> Lesson:
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 13),
        topic="Функции",
    )
    lesson.pipeline.processing_mode = processing_mode
    pipeline.create(lesson)
    lesson.transition(JobStatus.RECORDED)
    pipeline.save_state(lesson, "status", "error")
    return lesson


def test_transcription_failure_is_persisted(monkeypatch, tmp_path) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(pipeline)
    audio = tmp_path / "lesson.wav"
    audio.touch()
    monkeypatch.setattr(pipeline, "transcriber", lambda: FailingTranscriber())

    with pytest.raises(RuntimeError, match="model failure"):
        pipeline.transcribe(lesson, audio)

    restored = pipeline.store.get(lesson.lesson_id)
    assert restored.status == JobStatus.FAILED
    assert "model failure" in restored.error


def test_empty_asr_fails_without_automatic_revision_or_publication_job(
    monkeypatch,
    tmp_path,
) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"audio")
    (tmp_path / "audio_quality_report.json").write_text(
        json.dumps(
            {
                "ready": True,
                "microphone": {
                    "duration_seconds": 180.0,
                    "silence_ratio": 0.2,
                    "rms": 0.03,
                },
            }
        ),
        encoding="utf-8",
    )
    transcriber = EmptyTranscriber()
    monkeypatch.setattr(pipeline, "transcriber", lambda: transcriber)

    with pytest.raises(EmptyTranscriptionError, match="segment_count=0"):
        pipeline.transcribe(lesson, audio)

    restored = pipeline.store.get(lesson.lesson_id)
    assert restored is not None
    assert restored.status == JobStatus.FAILED
    assert "audio_quality_ready=True" in (restored.error or "")
    assert restored.artifacts.cleaned_transcript is None
    assert (
        pipeline.content_service.repository.list_transcript_revisions(lesson.lesson_id)
        == []
    )
    assert pipeline.store.list_automatic_publication_jobs() == []


def test_empty_durable_artifacts_are_not_reused(monkeypatch, tmp_path) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(pipeline)
    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"audio")
    EmptyTranscriber().transcribe(
        audio,
        pipeline.lesson_dir(lesson) / "transcript",
    )
    lesson.transition(JobStatus.TRANSCRIBING)
    pipeline.save_state(lesson, "status", "error")
    transcriber = DurableTranscriber()
    monkeypatch.setattr(pipeline, "transcriber", lambda: transcriber)

    recovered = pipeline.transcribe(lesson, audio)

    assert transcriber.calls == 1
    assert recovered.status == JobStatus.REVIEW_REQUIRED


def test_valid_retry_repairs_legacy_empty_unpublished_intent(
    monkeypatch,
    tmp_path,
) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    transcript_path = pipeline.lesson_dir(lesson) / "transcript" / "transcript_verified.txt"
    empty_revision = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "",
        path=transcript_path,
        created_by="automatic-transcription",
    )
    repository_path = "students/student/transcript/13.07.26.txt"
    pipeline.store.ensure_automatic_publication_job(
        lesson.lesson_id,
        empty_revision.revision_number,
        empty_revision.content_sha256,
        repository_path,
    )
    pipeline.store.update_automatic_publication_job(
        lesson.lesson_id,
        "blocked",
        error="legacy empty transcript",
        increment_attempts=True,
    )

    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(pipeline, "transcriber", lambda: DurableTranscriber())

    result = pipeline.transcribe(lesson, audio)

    revisions = pipeline.content_service.repository.list_transcript_revisions(
        lesson.lesson_id
    )
    valid_revision = revisions[0]
    repaired = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert result.status == JobStatus.REVIEW_REQUIRED
    assert valid_revision.content == "clean transcript\n"
    assert repaired is not None
    assert repaired.revision_number == valid_revision.revision_number
    assert repaired.content_sha256 == valid_revision.content_sha256
    assert repaired.status == "waiting"
    assert repaired.attempts == 0
    assert repaired.error is None


def test_final_persistence_failure_reconciles_without_second_asr(monkeypatch, tmp_path) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(pipeline)
    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"audio")
    transcriber = DurableTranscriber()
    monkeypatch.setattr(pipeline, "transcriber", lambda: transcriber)

    original_save_state = pipeline.save_state
    calls = 0

    def fail_final_save(current, *fields, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("database unavailable after artifacts")
        return original_save_state(current, *fields, **kwargs)

    monkeypatch.setattr(pipeline, "save_state", fail_final_save)

    with pytest.raises(RuntimeError, match="database unavailable after artifacts"):
        pipeline.transcribe(lesson, audio)

    assert transcriber.calls == 1
    persisted = pipeline.content_service.get_lesson(lesson.lesson_id).lesson
    assert persisted.status == JobStatus.TRANSCRIBING

    recovered = pipeline.transcribe(persisted, audio)

    assert transcriber.calls == 1
    assert recovered.status == JobStatus.REVIEW_REQUIRED
    stored = pipeline.content_service.get_lesson(lesson.lesson_id).lesson
    assert stored.status == JobStatus.REVIEW_REQUIRED
    assert stored.artifacts.transcription_manifest


def test_automatic_transcription_creates_canonical_immutable_revision(
    monkeypatch,
    tmp_path,
) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(pipeline, "transcriber", lambda: DurableTranscriber())

    result = pipeline.transcribe(lesson, audio)

    revisions = pipeline.content_service.repository.list_transcript_revisions(lesson.lesson_id)
    assert result.status == JobStatus.REVIEW_REQUIRED
    assert len(revisions) == 1
    revision = revisions[0]
    assert revision.created_by == "automatic-transcription"
    assert revision.content == "clean transcript\n"
    assert revision.content_sha256 == hashlib.sha256(b"clean transcript\n").hexdigest()
    assert result.status != JobStatus.READY
    jobs = pipeline.store.list_automatic_publication_jobs()
    assert len(jobs) == 1
    assert jobs[0].lesson_id == lesson.lesson_id
    assert jobs[0].revision_number == revision.revision_number
    assert jobs[0].content_sha256 == revision.content_sha256
    assert jobs[0].repository_path == "students/student/transcript/13.07.26.txt"
    assert jobs[0].status == "waiting"


def test_manual_transcription_does_not_create_automatic_revision(monkeypatch, tmp_path) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(pipeline)
    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(pipeline, "transcriber", lambda: DurableTranscriber())

    pipeline.transcribe(lesson, audio)

    assert pipeline.content_service.repository.list_transcript_revisions(lesson.lesson_id) == []


def test_automatic_revision_is_not_duplicated_during_artifact_reconciliation(
    monkeypatch,
    tmp_path,
) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"audio")
    transcriber = DurableTranscriber()
    monkeypatch.setattr(pipeline, "transcriber", lambda: transcriber)

    original_save_state = pipeline.save_state
    calls = 0

    def fail_final_save(current, *fields, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("database unavailable after automatic revision")
        return original_save_state(current, *fields, **kwargs)

    monkeypatch.setattr(pipeline, "save_state", fail_final_save)

    with pytest.raises(RuntimeError, match="database unavailable after automatic revision"):
        pipeline.transcribe(lesson, audio)

    revisions_after_failure = pipeline.content_service.repository.list_transcript_revisions(
        lesson.lesson_id
    )
    assert transcriber.calls == 1
    assert len(revisions_after_failure) == 1
    jobs_after_failure = pipeline.store.list_automatic_publication_jobs()
    assert len(jobs_after_failure) == 1

    persisted = pipeline.content_service.get_lesson(lesson.lesson_id).lesson
    recovered = pipeline.transcribe(persisted, audio)

    revisions_after_recovery = pipeline.content_service.repository.list_transcript_revisions(
        lesson.lesson_id
    )
    assert transcriber.calls == 1
    assert recovered.status == JobStatus.REVIEW_REQUIRED
    assert len(revisions_after_recovery) == 1
    assert revisions_after_recovery[0].created_by == "automatic-transcription"


def test_automatic_publication_uses_exact_durable_revision(
    monkeypatch,
    tmp_path,
) -> None:
    config = AppConfig(workspace=tmp_path)
    config.repository = RepositoryConfig(
        students_repo=tmp_path / "public-pages",
        repository_full_name="ArtemLevin/students-26-27",
        push=True,
    )
    config.automatic_transcript_repository = RepositoryConfig(
        students_repo=tmp_path / "private-transcripts",
        repository_full_name="ArtemLevin/private-student-transcripts",
        push=True,
    )
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(pipeline, "transcriber", lambda: DurableTranscriber())
    transcribed = pipeline.transcribe(lesson, audio)
    job = pipeline.store.list_automatic_publication_jobs()[0]
    observed = {}

    class FakePublisher:
        def __init__(self, publisher_config) -> None:
            observed["publisher_config"] = publisher_config

        def publish_payload(
            self,
            current,
            _lesson_dir,
            payload,
            *,
            reject_existing_mismatch,
        ):
            observed["payload"] = payload
            observed["reject_existing_mismatch"] = reject_existing_mismatch
            current.transition(JobStatus.PUBLISHED)
            return PublicationResult(
                branch="main",
                repository_path=payload.repository_path,
                commit="1" * 40,
                repository_full_name="ArtemLevin/private-students",
                content_sha256=payload.content_sha256,
                remote_verified=True,
            )

    monkeypatch.setattr(pipeline_module, "LessonPublisher", FakePublisher)

    result = pipeline.publish_automatic_transcript(
        transcribed,
        revision_number=job.revision_number,
        content_sha256=job.content_sha256,
        repository_path=job.repository_path,
    )

    payload = observed["payload"]
    assert observed["publisher_config"] is config.automatic_transcript_repository
    assert (
        observed["publisher_config"].repository_full_name
        == "ArtemLevin/private-student-transcripts"
    )
    assert observed["reject_existing_mismatch"] is True
    assert payload.revision_number == job.revision_number
    assert payload.content_sha256 == job.content_sha256
    assert payload.repository_path == job.repository_path
    assert result.remote_verified is True
    persisted = pipeline.content_service.get_lesson(lesson.lesson_id).lesson
    assert persisted.status == JobStatus.PUBLISHED
    assert persisted.publication is not None
    assert persisted.publication.content_sha256 == job.content_sha256


def test_automatic_publication_rejects_teacher_revision_before_transport(
    monkeypatch,
    tmp_path,
) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(pipeline, "transcriber", lambda: DurableTranscriber())
    transcribed = pipeline.transcribe(lesson, audio)
    teacher_revision = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "teacher edit",
        path=transcribed.artifacts.verified_transcript,
        created_by="teacher-review",
    )

    with pytest.raises(RuntimeError, match="automatic-transcription revision"):
        pipeline.publish_automatic_transcript(
            transcribed,
            revision_number=teacher_revision.revision_number,
            content_sha256=teacher_revision.content_sha256,
            repository_path="students/student/transcript/13.07.26.txt",
        )


def test_reconciliation_marks_inactive_job_with_missing_revision_history_conflict(
    tmp_path,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path))
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    repository_path = "students/student/transcript/13.07.26.txt"
    pipeline.store.ensure_automatic_publication_job(
        lesson.lesson_id,
        1,
        "a" * 64,
        repository_path,
    )

    reconciled = pipeline.reconcile_automatic_publication_intents()

    stored = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert reconciled == 1
    assert stored is not None
    assert stored.status == "conflict"
    assert "missing automatic transcript revision" in (stored.error or "")


def test_runtime_reconciliation_keeps_running_job_with_missing_revision_history(
    tmp_path,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path))
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    repository_path = "students/student/transcript/13.07.26.txt"
    pipeline.store.ensure_automatic_publication_job(
        lesson.lesson_id,
        1,
        "a" * 64,
        repository_path,
    )
    pipeline.store.update_automatic_publication_job(
        lesson.lesson_id,
        "running",
        increment_attempts=True,
    )

    reconciled = pipeline.reconcile_automatic_publication_intents()

    stored = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert reconciled == 0
    assert stored is not None
    assert stored.status == "running"
    assert stored.attempts == 1


def test_startup_reconciliation_conflicts_stale_running_job_with_missing_revision_history(
    tmp_path,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path))
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    repository_path = "students/student/transcript/13.07.26.txt"
    pipeline.store.ensure_automatic_publication_job(
        lesson.lesson_id,
        1,
        "a" * 64,
        repository_path,
    )
    pipeline.store.update_automatic_publication_job(
        lesson.lesson_id,
        "running",
        increment_attempts=True,
    )

    reconciled = pipeline.reconcile_automatic_publication_intents(
        recover_stale_running=True,
    )

    stored = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert reconciled == 1
    assert stored is not None
    assert stored.status == "conflict"
    assert stored.attempts == 1
    assert "missing automatic transcript revision" in (stored.error or "")


def test_reconciliation_preserves_valid_pinned_job_when_newer_revision_is_empty(
    tmp_path,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path))
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    transcript_path = pipeline.lesson_dir(lesson) / "transcript" / "transcript_verified.txt"
    valid_revision = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "valid transcript",
        path=transcript_path,
        created_by="automatic-transcription",
    )
    repository_path = "students/student/transcript/13.07.26.txt"
    pipeline.store.ensure_automatic_publication_job(
        lesson.lesson_id,
        valid_revision.revision_number,
        valid_revision.content_sha256,
        repository_path,
    )
    empty_revision = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "",
        path=transcript_path,
        created_by="automatic-transcription",
    )

    reconciled = pipeline.reconcile_automatic_publication_intents()

    stored = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert empty_revision.revision_number > valid_revision.revision_number
    assert reconciled == 0
    assert stored is not None
    assert stored.revision_number == valid_revision.revision_number
    assert stored.content_sha256 == valid_revision.content_sha256
    assert stored.status == "waiting"


def test_reconciliation_preserves_valid_pinned_job_when_newer_revision_is_meaningful(
    tmp_path,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path))
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    transcript_path = pipeline.lesson_dir(lesson) / "transcript" / "transcript_verified.txt"
    pinned = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "first valid transcript",
        path=transcript_path,
        created_by="automatic-transcription",
    )
    repository_path = "students/student/transcript/13.07.26.txt"
    pipeline.store.ensure_automatic_publication_job(
        lesson.lesson_id,
        pinned.revision_number,
        pinned.content_sha256,
        repository_path,
    )
    latest = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "newer valid transcript",
        path=transcript_path,
        created_by="automatic-transcription",
    )

    reconciled = pipeline.reconcile_automatic_publication_intents()

    stored = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert latest.revision_number > pinned.revision_number
    assert reconciled == 0
    assert stored is not None
    assert stored.revision_number == pinned.revision_number
    assert stored.content_sha256 == pinned.content_sha256
    assert stored.status == "waiting"


def test_reconciliation_repairs_empty_pinned_job_to_newer_meaningful_revision(
    tmp_path,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path))
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    transcript_path = pipeline.lesson_dir(lesson) / "transcript" / "transcript_verified.txt"
    empty_revision = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "",
        path=transcript_path,
        created_by="automatic-transcription",
    )
    repository_path = "students/student/transcript/13.07.26.txt"
    pipeline.store.ensure_automatic_publication_job(
        lesson.lesson_id,
        empty_revision.revision_number,
        empty_revision.content_sha256,
        repository_path,
    )
    valid_revision = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "recovered transcript",
        path=transcript_path,
        created_by="automatic-transcription",
    )

    reconciled = pipeline.reconcile_automatic_publication_intents()

    stored = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert reconciled == 1
    assert stored is not None
    assert stored.revision_number == valid_revision.revision_number
    assert stored.content_sha256 == valid_revision.content_sha256
    assert stored.status == "waiting"
    assert stored.attempts == 0
    assert stored.error is None


def test_runtime_reconciliation_does_not_repair_live_running_empty_job(
    tmp_path,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path))
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    transcript_path = pipeline.lesson_dir(lesson) / "transcript" / "transcript_verified.txt"
    empty_revision = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "",
        path=transcript_path,
        created_by="automatic-transcription",
    )
    repository_path = "students/student/transcript/13.07.26.txt"
    pipeline.store.ensure_automatic_publication_job(
        lesson.lesson_id,
        empty_revision.revision_number,
        empty_revision.content_sha256,
        repository_path,
    )
    pipeline.store.update_automatic_publication_job(
        lesson.lesson_id,
        "running",
        increment_attempts=True,
    )
    pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "new valid transcript",
        path=transcript_path,
        created_by="automatic-transcription",
    )

    reconciled = pipeline.reconcile_automatic_publication_intents()

    stored = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert reconciled == 0
    assert stored is not None
    assert stored.revision_number == empty_revision.revision_number
    assert stored.content_sha256 == empty_revision.content_sha256
    assert stored.status == "running"
    assert stored.attempts == 1


def test_startup_reconciliation_repairs_stale_running_empty_job(
    tmp_path,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path))
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    transcript_path = pipeline.lesson_dir(lesson) / "transcript" / "transcript_verified.txt"
    empty_revision = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "",
        path=transcript_path,
        created_by="automatic-transcription",
    )
    repository_path = "students/student/transcript/13.07.26.txt"
    pipeline.store.ensure_automatic_publication_job(
        lesson.lesson_id,
        empty_revision.revision_number,
        empty_revision.content_sha256,
        repository_path,
    )
    pipeline.store.update_automatic_publication_job(
        lesson.lesson_id,
        "running",
        increment_attempts=True,
    )
    valid_revision = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "new valid transcript",
        path=transcript_path,
        created_by="automatic-transcription",
    )

    reconciled = pipeline.reconcile_automatic_publication_intents(
        recover_stale_running=True,
    )

    stored = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert reconciled == 2
    assert stored is not None
    assert stored.revision_number == valid_revision.revision_number
    assert stored.content_sha256 == valid_revision.content_sha256
    assert stored.status == "waiting"
    assert stored.attempts == 0
    assert stored.error is None


def test_startup_reconciliation_blocks_stale_running_empty_job_without_replacement(
    tmp_path,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path))
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    transcript_path = pipeline.lesson_dir(lesson) / "transcript" / "transcript_verified.txt"
    empty_revision = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "",
        path=transcript_path,
        created_by="automatic-transcription",
    )
    repository_path = "students/student/transcript/13.07.26.txt"
    pipeline.store.ensure_automatic_publication_job(
        lesson.lesson_id,
        empty_revision.revision_number,
        empty_revision.content_sha256,
        repository_path,
    )
    pipeline.store.update_automatic_publication_job(
        lesson.lesson_id,
        "running",
        increment_attempts=True,
    )

    reconciled = pipeline.reconcile_automatic_publication_intents(
        recover_stale_running=True,
    )

    stored = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert reconciled == 1
    assert stored is not None
    assert stored.revision_number == empty_revision.revision_number
    assert stored.status == "blocked"
    assert stored.attempts == 1
    assert "транскрипт пуст" in (stored.error or "")


def test_reconciliation_heals_false_empty_block_on_meaningful_pinned_job(
    tmp_path,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path))
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    transcript_path = pipeline.lesson_dir(lesson) / "transcript" / "transcript_verified.txt"
    pinned = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "valid transcript",
        path=transcript_path,
        created_by="automatic-transcription",
    )
    repository_path = "students/student/transcript/13.07.26.txt"
    pipeline.store.ensure_automatic_publication_job(
        lesson.lesson_id,
        pinned.revision_number,
        pinned.content_sha256,
        repository_path,
    )
    pipeline.store.update_automatic_publication_job(
        lesson.lesson_id,
        "blocked",
        error=pipeline_module._EMPTY_AUTOMATIC_TRANSCRIPT_ERROR,
    )
    pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "",
        path=transcript_path,
        created_by="automatic-transcription",
    )

    reconciled = pipeline.reconcile_automatic_publication_intents()

    stored = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert reconciled == 1
    assert stored is not None
    assert stored.revision_number == pinned.revision_number
    assert stored.status == "waiting"
    assert stored.error is None


def test_startup_reconciliation_recreates_missing_publication_intent(
    monkeypatch,
    tmp_path,
) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(pipeline, "transcriber", lambda: DurableTranscriber())
    pipeline.transcribe(lesson, audio)

    with pipeline.store.connect() as db:
        db.execute(
            "DELETE FROM automatic_publication_jobs WHERE lesson_id=?",
            (lesson.lesson_id,),
        )

    reconciled = pipeline.reconcile_automatic_publication_intents()

    restored = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert reconciled == 1
    assert restored is not None
    assert restored.status == "waiting"
    assert restored.repository_path == "students/student/transcript/13.07.26.txt"


def test_startup_reconciliation_does_not_create_job_for_empty_revision(tmp_path) -> None:
    config = AppConfig(workspace=tmp_path)
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    empty_revision = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "",
        path=pipeline.lesson_dir(lesson) / "transcript" / "transcript_verified.txt",
        created_by="automatic-transcription",
    )

    reconciled = pipeline.reconcile_automatic_publication_intents()

    assert empty_revision.content == "\n"
    assert reconciled == 0
    assert pipeline.store.get_automatic_publication_job(lesson.lesson_id) is None


def test_startup_reconciliation_blocks_existing_empty_publication_job(tmp_path) -> None:
    config = AppConfig(workspace=tmp_path)
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(
        pipeline,
        processing_mode=LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB,
    )
    empty_revision = pipeline.content_service.save_transcript(
        lesson.lesson_id,
        "",
        path=pipeline.lesson_dir(lesson) / "transcript" / "transcript_verified.txt",
        created_by="automatic-transcription",
    )
    pipeline.store.ensure_automatic_publication_job(
        lesson.lesson_id,
        empty_revision.revision_number,
        empty_revision.content_sha256,
        "students/student/transcript/13.07.26.txt",
    )

    reconciled = pipeline.reconcile_automatic_publication_intents()

    stored = pipeline.store.get_automatic_publication_job(lesson.lesson_id)
    assert reconciled == 1
    assert stored is not None
    assert stored.status == "blocked"
    assert "транскрипт пуст" in (stored.error or "")
