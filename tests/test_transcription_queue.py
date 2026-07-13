from datetime import date
from pathlib import Path

from tutor_assistant.domain import Lesson, Student
from tutor_assistant.store import LessonStore
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


def test_queue_state_is_persisted_and_failed_job_can_be_retried(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    source = lesson("persistent")
    store.save(source)
    audio = tmp_path / "lesson.wav"
    audio.touch()
    queue = TranscriptionQueue(store)

    job = queue.enqueue(source, audio)
    queue.start_next()
    queue.fail(job.id, "temporary failure")

    stored = store.list_transcription_jobs()[0]
    assert stored.status == QueueStatus.FAILED
    assert stored.attempts == 1

    restored = TranscriptionQueue(store)
    restored.restore(source, Path(stored.audio_path), QueueStatus(stored.status), stored.error)
    restored.retry(source.lesson_id)

    assert restored.start_next().id == source.lesson_id
    assert store.list_transcription_jobs()[0].attempts == 2
