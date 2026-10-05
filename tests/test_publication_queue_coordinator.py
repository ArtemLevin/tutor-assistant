from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tutor_assistant.application.publication_queue import (
    PublicationPumpContext,
    PublicationQueueCoordinator,
)
from tutor_assistant.domain import Lesson, Student
from tutor_assistant.publication_queue import AutomaticPublicationStatus


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
