from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
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


def test_invalid_content_size_preserves_database_constraint_error(tmp_path: Path) -> None:
    store = PublicationOperationStore(tmp_path / "publication.sqlite3")

    with pytest.raises(sqlite3.IntegrityError, match="content_size_bytes >= 0"):
        store.begin(
            lesson_id="lesson-1",
            repository_full_name="ArtemLevin/students-26-27",
            remote_name="origin",
            remote_url_sha256="a" * 64,
            branch="main",
            repository_path="transcript.txt",
            content_sha256="b" * 64,
            content_size_bytes=-1,
            expected_remote_sha="1" * 40,
        )

    assert store.active_for_lesson("lesson-1") is None


def test_invalid_content_size_is_not_disguised_by_existing_active_operation(tmp_path: Path) -> None:
    store = PublicationOperationStore(tmp_path / "publication.sqlite3")
    active = begin(store)

    with pytest.raises(sqlite3.IntegrityError, match="content_size_bytes >= 0"):
        store.begin(
            lesson_id="lesson-1",
            repository_full_name="ArtemLevin/students-26-27",
            remote_name="origin",
            remote_url_sha256="a" * 64,
            branch="main",
            repository_path="transcript.txt",
            content_sha256="b" * 64,
            content_size_bytes=-1,
            expected_remote_sha="1" * 40,
        )

    assert store.active_for_lesson("lesson-1") == active


@pytest.mark.parametrize(
    ("transition", "arguments", "expected_status"),
    [
        ("mark_completed", (), "completed"),
        ("mark_remote_verified", ("2" * 40,), "remote_verified"),
        ("mark_indeterminate", (), "indeterminate"),
    ],
)
def test_publication_rejects_invalid_transition_without_mutating_active_operation(
    tmp_path: Path,
    transition: str,
    arguments: tuple[str, ...],
    expected_status: str,
) -> None:
    store = PublicationOperationStore(tmp_path / "publication.sqlite3")
    operation = begin(store)
    method = getattr(store, transition)
    kwargs = (
        {"error_code": "network", "details": "verification unavailable"}
        if transition == "mark_indeterminate"
        else {}
    )

    with pytest.raises(PublicationOperationConflict, match=f"prepared.*{expected_status}"):
        method(operation.id, *arguments, **kwargs)

    assert store.get(operation.id) == operation
    assert store.active_for_lesson(operation.lesson_id) == operation


def test_publication_remote_verification_can_resume_prepared_operation(tmp_path: Path) -> None:
    store = PublicationOperationStore(tmp_path / "publication.sqlite3")
    operation = begin(store)

    verified = store.mark_remote_verified(operation.id, "2" * 40, allow_prepared=True)
    completed = store.mark_completed(operation.id)

    assert verified.status == PublicationOperationStatus.REMOTE_VERIFIED
    assert verified.remote_commit_sha == "2" * 40
    assert verified.remote_verified_at is not None
    assert completed.status == PublicationOperationStatus.COMPLETED
    assert store.active_for_lesson(operation.lesson_id) is None


def test_indeterminate_publication_can_be_verified_without_repeating_push(tmp_path: Path) -> None:
    store = PublicationOperationStore(tmp_path / "publication.sqlite3")
    operation = begin(store)
    store.mark_pushing(operation.id, "2" * 40)
    store.mark_indeterminate(
        operation.id,
        error_code="remote_unavailable",
        details="transport timed out",
    )

    recovered = store.mark_remote_verified(operation.id, "2" * 40)

    assert recovered.status == PublicationOperationStatus.REMOTE_VERIFIED
    assert recovered.local_commit_sha == recovered.remote_commit_sha == "2" * 40
    assert recovered.error_code is None
    assert recovered.error_details is None


@pytest.mark.parametrize("terminal_status", ["failed", "conflict"])
def test_terminal_publication_errors_preserve_context_and_allow_retry(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    store = PublicationOperationStore(tmp_path / "publication.sqlite3")
    operation = begin(store)
    details = "prefix-to-truncate" + "x" * 3000

    if terminal_status == "failed":
        terminal = store.mark_failed(operation.id, error_code="push_failed", details=details)
        assert terminal.error_code == "push_failed"
    else:
        terminal = store.mark_conflict(
            operation.id,
            remote_commit_sha="3" * 40,
            details=details,
        )
        assert terminal.error_code == "remote_advanced"
        assert terminal.remote_commit_sha == "3" * 40

    assert terminal.status.value == terminal_status
    assert terminal.error_details == "x" * 3000
    assert terminal.completed_at is not None
    replacement = begin(store)
    assert replacement.id != operation.id
    assert store.get(operation.id) == terminal


def test_concurrent_publication_begins_allow_exactly_one_active_operation(tmp_path: Path) -> None:
    store = PublicationOperationStore(tmp_path / "publication.sqlite3")
    ready = threading.Barrier(2)

    def begin_after_barrier():
        ready.wait(timeout=5)
        try:
            return begin(store)
        except PublicationOperationConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=10) for future in [executor.submit(begin_after_barrier) for _ in range(2)]
        ]

    operations = [result for result in results if not isinstance(result, PublicationOperationConflict)]
    conflicts = [result for result in results if isinstance(result, PublicationOperationConflict)]
    assert len(operations) == len(conflicts) == 1
    assert store.active_for_lesson("lesson-1") == operations[0]


def test_unknown_publication_operation_preserves_lookup_error(tmp_path: Path) -> None:
    store = PublicationOperationStore(tmp_path / "publication.sqlite3")

    with pytest.raises(LookupError, match="Publication operation not found: missing"):
        store.mark_completed("missing")
