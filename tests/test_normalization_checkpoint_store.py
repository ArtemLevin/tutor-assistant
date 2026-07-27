
from __future__ import annotations

from datetime import UTC, date, datetime

from tutor_assistant.content import StudentContentService
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.normalization.checkpoints import NormalizationCheckpointStore
from tutor_assistant.normalization.chunking import chunk_segments
from tutor_assistant.normalization.models import (
    NormalizationChunkStatus,
    NormalizationQuality,
    SourceSegment,
)


def _run_id(service: StudentContentService) -> int:
    lesson = Lesson(
        lesson_id="checkpoint-store",
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        topic="Дроби",
        lesson_date=date(2026, 7, 27),
        status=JobStatus.REVIEW_REQUIRED,
    )
    service.create_lesson(lesson)
    with service.repository.connect() as db:
        cursor = db.execute(
            """
            INSERT INTO normalization_runs (
                lesson_id, source_sha256, model, prompt_version,
                configuration_hash, provider, status, attempts, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?)
            """,
            (
                lesson.lesson_id,
                "a" * 64,
                "model",
                "prompt.v1",
                "b" * 64,
                "ollama",
                datetime(2026, 7, 27, tzinfo=UTC).isoformat(),
            ),
        )
        return int(cursor.lastrowid)


def test_checkpoint_store_persists_empty_completed_text(tmp_path) -> None:
    content = StudentContentService(tmp_path / "data")
    run_id = _run_id(content)
    chunks = chunk_segments(
        [SourceSegment(source_segment_id=1, speaker="П", text="служебная реплика")],
        max_segments=1,
        max_characters=100,
        overlap_segments=0,
    )
    store = NormalizationCheckpointStore(content.repository)
    checkpoints = store.prepare_chunks(
        run_id,
        chunks,
        configuration_hash="b" * 64,
        prompt_version="prompt.v1",
        subject_profile="mathematics",
    )
    assert checkpoints[0].status == NormalizationChunkStatus.PENDING

    store.mark_running(run_id, 0)
    store.complete(
        run_id,
        0,
        normalized_text="",
        quality=NormalizationQuality(
            plain_text_valid=True,
            numbers_preserved=True,
            formula_tokens_preserved=True,
            protected_content_preserved=True,
            requires_manual_attention=False,
        ),
    )

    checkpoint = store.get(run_id, 0)
    assert checkpoint is not None
    assert checkpoint.status == NormalizationChunkStatus.COMPLETED
    assert checkpoint.normalized_text == ""
    assert checkpoint.attempts == 1
    store.verify_completed(checkpoint)


def test_running_checkpoint_recovers_as_indeterminate(tmp_path) -> None:
    content = StudentContentService(tmp_path / "data")
    run_id = _run_id(content)
    chunks = chunk_segments(
        [SourceSegment(source_segment_id=1, speaker="П", text="x = 2")],
        max_segments=1,
        max_characters=100,
        overlap_segments=0,
    )
    store = NormalizationCheckpointStore(content.repository)
    store.prepare_chunks(
        run_id,
        chunks,
        configuration_hash="b" * 64,
        prompt_version="prompt.v1",
        subject_profile="mathematics",
    )
    store.mark_running(run_id, 0)

    assert store.recover_interrupted() == 1
    checkpoint = store.get(run_id, 0)
    assert checkpoint and checkpoint.status == NormalizationChunkStatus.INDETERMINATE
