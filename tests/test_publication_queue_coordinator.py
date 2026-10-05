from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tutor_assistant.application.publication_queue import (
    PublicationFailureDisposition,
    PublicationPumpContext,
    PublicationQueueCoordinator,
    PublicationSubmission,
    classify_publication_failure,
)
from tutor_assistant.domain import Lesson, Student
from tutor_assistant.publication_queue import AutomaticPublicationStatus
from tutor_assistant.publisher import (
    GitError,
    PublicationBlockedError,
    PublicationConflictError,
)
from tutor_assistant.ui.publication_worker import PublicationWorker


def lesson(identifier: str) -> Lesson:
    return Lesson(
        lesson_id=identifier,
        student=Student(id=f"student_{identifier}", full_name=identifier),
        subject="mathematics",
        lesson_date=date(2026, 10, 4),
        topic=f"Тема {identifier}",
    )


def enqueue(coordinator: PublicationQueueCoordinator, identifier: str):
    return coordinator.enqueue(
        lesson(identifier),
        revision_number=1,
        content_sha256="a" * 64,
        repository_path=f"students/{identifier}/transcript/04.10.26.txt",
    )


def test_publication_jobs_run_sequentially() -> None:
    coordinator = PublicationQueueCoordinator()
    enqueue(coordinator, "first")
    enqueue(coordinator, "second")

    first = coordinator.pump(PublicationPumpContext())
    assert first is not None
    assert first.job_id == "first"
    assert coordinator.pump(PublicationPumpContext()) is None

    coordinator.published(first.job_id)
    second = coordinator.pump(PublicationPumpContext())
    assert second is not None
    assert second.job_id == "second"


def test_publication_enqueue_deduplicates_immutable_payload() -> None:
    coordinator = PublicationQueueCoordinator()

    first = enqueue(coordinator, "lesson")
    second = enqueue(coordinator, "lesson")

    assert first is second
    with pytest.raises(ValueError, match="immutable"):
        coordinator.enqueue(
            lesson("lesson"),
            revision_number=2,
            content_sha256="b" * 64,
            repository_path="students/lesson/transcript/04.10.26.txt",
        )


def test_retry_required_waits_until_due() -> None:
    coordinator = PublicationQueueCoordinator()
    enqueue(coordinator, "lesson")
    started = coordinator.pump(PublicationPumpContext())
    assert started is not None
    now = datetime.now(UTC)
    coordinator.retry_required(
        started.job_id,
        "network",
        next_attempt_at=now + timedelta(minutes=2),
    )

    assert coordinator.pump(PublicationPumpContext(), now=now) is None
    retried = coordinator.pump(
        PublicationPumpContext(),
        now=now + timedelta(minutes=3),
    )
    assert retried is not None
    assert retried.attempts == 2


def test_conflict_is_terminal_and_not_automatically_retried() -> None:
    coordinator = PublicationQueueCoordinator()
    enqueue(coordinator, "lesson")
    started = coordinator.pump(PublicationPumpContext())
    assert started is not None

    coordinator.conflict(started.job_id, "different remote content")

    job = coordinator.get(started.job_id)
    assert job is not None
    assert job.status == AutomaticPublicationStatus.CONFLICT
    assert coordinator.pump(PublicationPumpContext()) is None
    with pytest.raises(ValueError, match="not retryable"):
        coordinator.retry(started.job_id)


def test_blocked_requires_explicit_retry() -> None:
    coordinator = PublicationQueueCoordinator()
    enqueue(coordinator, "lesson")
    started = coordinator.pump(PublicationPumpContext())
    assert started is not None
    coordinator.blocked(started.job_id, "repository is public")

    assert coordinator.pump(PublicationPumpContext()) is None
    coordinator.retry(started.job_id)
    assert coordinator.pump(PublicationPumpContext()) is not None


def test_shutdown_barrier_prevents_new_publication() -> None:
    coordinator = PublicationQueueCoordinator()
    enqueue(coordinator, "lesson")

    assert coordinator.pump(PublicationPumpContext(shutdown_requested=True)) is None



def test_publication_failure_classifier_separates_conflict_block_and_retry() -> None:
    conflict = classify_publication_failure(
        PublicationConflictError("collision"),
        attempts=1,
    )
    blocked = classify_publication_failure(
        PublicationBlockedError("public repository"),
        attempts=1,
    )
    transient = classify_publication_failure(GitError("network"), attempts=1)
    exhausted = classify_publication_failure(GitError("network"), attempts=5)

    assert conflict.disposition == PublicationFailureDisposition.CONFLICT
    assert blocked.disposition == PublicationFailureDisposition.BLOCKED
    assert transient.disposition == PublicationFailureDisposition.RETRY_REQUIRED
    assert transient.retry_after_seconds == 30
    assert exhausted.disposition == PublicationFailureDisposition.BLOCKED


def test_publication_failure_backoff_is_bounded() -> None:
    delays = [
        classify_publication_failure(GitError("temporary"), attempts=attempt).retry_after_seconds
        for attempt in range(1, 5)
    ]

    assert delays == [30, 120, 600, 1800]


def test_publication_worker_executes_exact_immutable_submission() -> None:
    observed = {}
    results = []
    source = lesson("worker")
    item = PublicationSubmission(
        job_id=source.lesson_id,
        lesson=source,
        revision_number=3,
        content_sha256="a" * 64,
        repository_path="students/worker/transcript/04.10.26.txt",
        attempts=1,
    )

    class Pipeline:
        def publish_automatic_transcript(
            self,
            current,
            *,
            revision_number,
            content_sha256,
            repository_path,
        ):
            observed.update(
                lesson=current,
                revision_number=revision_number,
                content_sha256=content_sha256,
                repository_path=repository_path,
            )
            return "published"

    worker = PublicationWorker(Pipeline())
    worker.succeeded.connect(
        lambda job_id, result: results.append((job_id, result))
    )
    worker.submit(item)
    worker.shutdown()

    worker.run()

    assert observed == {
        "lesson": item.lesson,
        "revision_number": item.revision_number,
        "content_sha256": item.content_sha256,
        "repository_path": item.repository_path,
    }
    assert results == [(item.job_id, "published")]
    assert not worker.busy


def test_publication_worker_preserves_exception_type_for_failure_policy() -> None:
    failures = []
    source = lesson("worker-failure")
    item = PublicationSubmission(
        job_id=source.lesson_id,
        lesson=source,
        revision_number=1,
        content_sha256="b" * 64,
        repository_path="students/worker-failure/transcript/04.10.26.txt",
        attempts=1,
    )

    class Pipeline:
        def publish_automatic_transcript(self, *_args, **_kwargs):
            raise RuntimeError("invalid publication configuration")

    worker = PublicationWorker(Pipeline())
    worker.failed.connect(
        lambda job_id, error, details: failures.append((job_id, error, details))
    )
    worker.submit(item)
    worker.shutdown()

    worker.run()

    assert len(failures) == 1
    job_id, error, details = failures[0]
    assert job_id == item.job_id
    assert isinstance(error, RuntimeError)
    assert str(error) == "invalid publication configuration"
    assert "RuntimeError: invalid publication configuration" in details
    assert not worker.busy
