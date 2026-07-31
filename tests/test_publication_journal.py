from __future__ import annotations

from pathlib import Path

import pytest

from tutor_assistant.publication import (
    PublicationOperationConflict,
    PublicationOperationStatus,
    PublicationOperationStore,
)


def begin(store: PublicationOperationStore):
    return store.begin(
        lesson_id="lesson-1",
        repository_full_name="ArtemLevin/students-26-27",
        remote_name="origin",
        remote_url_sha256="a" * 64,
        branch="main",
        repository_path="students/test/lessons/lesson__lesson-1/transcript.txt",
        content_sha256="b" * 64,
        content_size_bytes=42,
        expected_remote_sha="1" * 40,
    )


def test_publication_operation_happy_path(tmp_path: Path) -> None:
    store = PublicationOperationStore(tmp_path / "publication.sqlite3")
    operation = begin(store)

    pushing = store.mark_pushing(operation.id, "2" * 40)
    verified = store.mark_remote_verified(pushing.id, "2" * 40)
    completed = store.mark_completed(verified.id)

    assert operation.status == PublicationOperationStatus.PREPARED
    assert pushing.status == PublicationOperationStatus.PUSHING
    assert verified.status == PublicationOperationStatus.REMOTE_VERIFIED
    assert completed.status == PublicationOperationStatus.COMPLETED
    assert completed.remote_commit_sha == "2" * 40
    assert completed.completed_at is not None
    assert store.active_for_lesson("lesson-1") is None


def test_only_one_active_operation_per_lesson(tmp_path: Path) -> None:
    store = PublicationOperationStore(tmp_path / "publication.sqlite3")
    begin(store)

    with pytest.raises(PublicationOperationConflict):
        begin(store)


def test_failed_operation_allows_retry(tmp_path: Path) -> None:
    store = PublicationOperationStore(tmp_path / "publication.sqlite3")
    operation = begin(store)
    failed = store.mark_failed(
        operation.id,
        error_code="push_failed",
        details="transport error",
    )

    replacement = begin(store)

    assert failed.status == PublicationOperationStatus.FAILED
    assert replacement.id != operation.id


def test_indeterminate_operation_remains_active(tmp_path: Path) -> None:
    store = PublicationOperationStore(tmp_path / "publication.sqlite3")
    operation = begin(store)
    pushing = store.mark_pushing(operation.id, "2" * 40)
    indeterminate = store.mark_indeterminate(
        pushing.id,
        error_code="remote_unavailable",
        details="verification unavailable",
    )

    assert indeterminate.status == PublicationOperationStatus.INDETERMINATE
    assert store.active_for_lesson("lesson-1") == indeterminate
