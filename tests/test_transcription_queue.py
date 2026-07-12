from datetime import date
from pathlib import Path

from tutor_assistant.domain import Lesson, Student
from tutor_assistant.transcription_queue import QueueStatus, TranscriptionQueue


def lesson(identifier: str) -> Lesson:
    return Lesson(
        lesson_id=identifier,
        student=Student(id=f"student_{identifier}", full_name=identifier),
        subject="mathematics",
        lesson_date=date(2026, 7, 12),
        topic="Тема",
    )


def test_queue_runs_jobs_sequentially() -> None:
    queue = TranscriptionQueue()
    first = queue.enqueue(lesson("first"), Path("first.wav"))
    second = queue.enqueue(lesson("second"), Path("second.wav"))

    assert queue.start_next() is first
    assert queue.start_next() is None
    queue.complete(first.id, first.lesson)
    assert queue.start_next() is second
    assert queue.unfinished_count == 1


def test_queue_deduplicates_active_lesson() -> None:
    queue = TranscriptionQueue()
    source = lesson("same")

    first = queue.enqueue(source, Path("one.wav"))
    duplicate = queue.enqueue(source, Path("two.wav"))

    assert duplicate is first
    assert len(queue.jobs) == 1


def test_failed_job_does_not_block_next() -> None:
    queue = TranscriptionQueue()
    first = queue.enqueue(lesson("first"), Path("first.wav"))
    second = queue.enqueue(lesson("second"), Path("second.wav"))
    queue.start_next()

    queue.fail(first.id, "boom")

    assert first.status == QueueStatus.FAILED
    assert queue.start_next() is second
