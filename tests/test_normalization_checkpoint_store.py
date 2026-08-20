from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

import pytest

from tutor_assistant.content import StudentContentService
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.normalization.checkpoints import NormalizationCheckpointStore
from tutor_assistant.normalization.chunking import chunk_segments
from tutor_assistant.normalization.errors import NormalizationCheckpointMismatchError
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


def _prepared_store(tmp_path):
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
    return content, store, run_id, chunks


def _quality() -> NormalizationQuality:
    return NormalizationQuality(
        plain_text_valid=True,
        numbers_preserved=True,
        formula_tokens_preserved=True,
        protected_content_preserved=True,
        requires_manual_attention=False,
    )


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


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("mark_running", ()),
        ("fail", ("provider unavailable",)),
        ("reset_pending", ()),
        ("reset_indeterminate", ()),
        ("mark_indeterminate", ("unknown provider response",)),
    ],
)
def test_checkpoint_mutation_rejects_missing_chunk(tmp_path, operation, arguments) -> None:
    content = StudentContentService(tmp_path / "data")
    store = NormalizationCheckpointStore(content.repository)

    with pytest.raises(LookupError, match="Normalization chunk not found: 999999/0"):
        getattr(store, operation)(999999, 0, *arguments)

    assert store.get(999999, 0) is None


def test_completed_checkpoint_cannot_be_restarted_or_overwritten(tmp_path) -> None:
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
    quality = NormalizationQuality(
        plain_text_valid=True,
        numbers_preserved=True,
        formula_tokens_preserved=True,
        protected_content_preserved=True,
        requires_manual_attention=False,
    )
    store.complete(run_id, 0, normalized_text="[П] x = 2", quality=quality)

    with pytest.raises(NormalizationCheckpointMismatchError, match="completed.*running"):
        store.mark_running(run_id, 0)

    checkpoint = store.get(run_id, 0)
    assert checkpoint is not None
    assert checkpoint.status == NormalizationChunkStatus.COMPLETED
    assert checkpoint.normalized_text == "[П] x = 2"
    assert checkpoint.attempts == 1


def test_reset_indeterminate_rejects_unconfirmed_pending_chunk(tmp_path) -> None:
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

    with pytest.raises(NormalizationCheckpointMismatchError, match="pending.*pending"):
        store.reset_indeterminate(run_id, 0)

    checkpoint = store.get(run_id, 0)
    assert checkpoint is not None
    assert checkpoint.status == NormalizationChunkStatus.PENDING
    assert checkpoint.error is None


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("configuration_hash", "c" * 64),
        ("prompt_version", "prompt.v2"),
        ("subject_profile", "physics"),
    ],
)
def test_prepare_chunks_rejects_changed_execution_identity_without_data_loss(
    tmp_path,
    field,
    replacement,
) -> None:
    _content, store, run_id, chunks = _prepared_store(tmp_path)
    store.mark_running(run_id, 0)
    store.complete(run_id, 0, normalized_text="[П] x = 2", quality=_quality())
    original = store.get(run_id, 0)
    arguments = {
        "configuration_hash": "b" * 64,
        "prompt_version": "prompt.v1",
        "subject_profile": "mathematics",
    }
    arguments[field] = replacement

    with pytest.raises(NormalizationCheckpointMismatchError, match="не соответствует"):
        store.prepare_chunks(run_id, chunks, **arguments)

    assert store.get(run_id, 0) == original


def test_prepare_chunks_is_idempotent_and_preserves_completed_response(tmp_path) -> None:
    _content, store, run_id, chunks = _prepared_store(tmp_path)
    store.mark_running(run_id, 0)
    store.complete(run_id, 0, normalized_text="[П] x = 2", quality=_quality())

    prepared = store.prepare_chunks(
        run_id,
        chunks,
        configuration_hash="b" * 64,
        prompt_version="prompt.v1",
        subject_profile="mathematics",
    )

    assert len(prepared) == 1
    assert prepared[0].status == NormalizationChunkStatus.COMPLETED
    assert prepared[0].normalized_text == "[П] x = 2"
    assert prepared[0].attempts == 1


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("fail", ("must not overwrite completed response",)),
        ("reset_pending", ()),
        ("reset_indeterminate", ()),
        ("mark_indeterminate", ("must not replay cloud request",)),
    ],
)
def test_terminal_checkpoint_rejects_all_destructive_transitions(
    tmp_path,
    operation,
    arguments,
) -> None:
    _content, store, run_id, _chunks = _prepared_store(tmp_path)
    store.mark_running(run_id, 0)
    store.complete(run_id, 0, normalized_text="approved response", quality=_quality())
    original = store.get(run_id, 0)

    with pytest.raises(NormalizationCheckpointMismatchError, match="completed"):
        getattr(store, operation)(run_id, 0, *arguments)

    assert store.get(run_id, 0) == original


def test_checkpoint_retry_increments_attempts_and_clears_prior_error(tmp_path) -> None:
    _content, store, run_id, _chunks = _prepared_store(tmp_path)
    store.mark_running(run_id, 0)
    store.fail(run_id, 0, "prefix-to-truncate" + "x" * 2000)
    failed = store.get(run_id, 0)
    assert failed is not None
    assert failed.status == NormalizationChunkStatus.FAILED
    assert failed.error == "x" * 2000

    store.mark_running(run_id, 0)

    retried = store.get(run_id, 0)
    assert retried is not None
    assert retried.status == NormalizationChunkStatus.RUNNING
    assert retried.attempts == 2
    assert retried.error is None


def test_indeterminate_checkpoint_requires_explicit_reset_before_retry(tmp_path) -> None:
    _content, store, run_id, _chunks = _prepared_store(tmp_path)
    store.mark_running(run_id, 0)
    store.mark_indeterminate(run_id, 0, "cloud request may already have completed")

    with pytest.raises(NormalizationCheckpointMismatchError, match="indeterminate.*running"):
        store.mark_running(run_id, 0)

    store.reset_indeterminate(run_id, 0)
    confirmed = store.get(run_id, 0)
    assert confirmed is not None
    assert confirmed.status == NormalizationChunkStatus.PENDING
    assert confirmed.error == "indeterminate_retry_confirmed"
    store.mark_running(run_id, 0)
    retried = store.get(run_id, 0)
    assert retried is not None
    assert retried.attempts == 2
    assert retried.error is None


def test_completion_requires_running_checkpoint_and_preserves_pending_state(tmp_path) -> None:
    _content, store, run_id, _chunks = _prepared_store(tmp_path)

    with pytest.raises(NormalizationCheckpointMismatchError, match="pending.*completed"):
        store.complete(run_id, 0, normalized_text="unexpected", quality=_quality())

    checkpoint = store.get(run_id, 0)
    assert checkpoint is not None
    assert checkpoint.status == NormalizationChunkStatus.PENDING
    assert checkpoint.normalized_text is None


def test_verify_completed_rejects_persisted_response_checksum_mismatch(tmp_path) -> None:
    content, store, run_id, _chunks = _prepared_store(tmp_path)
    store.mark_running(run_id, 0)
    store.complete(run_id, 0, normalized_text="approved response", quality=_quality())
    with content.repository.connect() as db:
        db.execute(
            "UPDATE normalization_chunks SET normalized_text=? WHERE run_id=? AND chunk_index=?",
            ("silently altered response", run_id, 0),
        )
    corrupted = store.get(run_id, 0)
    assert corrupted is not None

    with pytest.raises(NormalizationCheckpointMismatchError, match="Контрольная сумма"):
        store.verify_completed(corrupted)


@pytest.mark.parametrize("field", ["normalized_text", "quality_json"])
def test_verify_completed_rejects_missing_required_persisted_payload(tmp_path, field) -> None:
    content, store, run_id, _chunks = _prepared_store(tmp_path)
    store.mark_running(run_id, 0)
    store.complete(run_id, 0, normalized_text="approved response", quality=_quality())
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        with content.repository.connect() as db:
            db.execute(
                f"UPDATE normalization_chunks SET {field}=NULL WHERE run_id=? AND chunk_index=?",
                (run_id, 0),
            )
    persisted = store.get(run_id, 0)
    assert persisted is not None
    assert persisted.normalized_text == "approved response"
    corrupted = persisted.model_copy(
        update={"normalized_text" if field == "normalized_text" else "quality": None}
    )

    with pytest.raises(NormalizationCheckpointMismatchError, match="не содержит обязательных данных"):
        store.verify_completed(corrupted)


def test_recover_interrupted_is_idempotent_and_does_not_replay_completed_chunks(tmp_path) -> None:
    _content, store, run_id, _chunks = _prepared_store(tmp_path)
    store.mark_running(run_id, 0)

    assert store.recover_interrupted() == 1
    assert store.recover_interrupted() == 0
    checkpoint = store.get(run_id, 0)
    assert checkpoint is not None
    assert checkpoint.status == NormalizationChunkStatus.INDETERMINATE
    assert checkpoint.error == "interrupted_during_provider_request"
