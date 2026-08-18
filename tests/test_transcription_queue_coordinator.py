from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from tutor_assistant.application.transcription_queue import (
    TranscriptionAudioMissingError,
    TranscriptionPumpContext,
    TranscriptionQueueCoordinator,
)
from tutor_assistant.domain import JobStatus, Lesson, Student


def lesson(identifier: str, *, status: JobStatus = JobStatus.DRAFT) -> Lesson:
    return Lesson(
        lesson_id=identifier,
        student=Student(id=f"student_{identifier}", full_name=identifier),
        subject="mathematics",
        lesson_date=date(2026, 8, 18),
        topic=f"Тема {identifier}",
        status=status,
    )


@dataclass(frozen=True)
class StoredJob:
    lesson_id: str
    audio_path: str
    status: str
    error: str | None = None


def test_pump_respects_shutdown_and_normalization_barriers(tmp_path: Path) -> None:
    coordinator = TranscriptionQueueCoordinator()
    first_audio = tmp_path / "first.wav"
    second_audio = tmp_path / "second.wav"
    first_audio.touch()
    second_audio.touch()
    coordinator.enqueue(lesson("first"), first_audio)
    coordinator.enqueue(lesson("second"), second_audio)

    assert coordinator.pump(TranscriptionPumpContext(shutdown_requested=True)) is None
    assert coordinator.pump(TranscriptionPumpContext(normalization_active=True)) is None

    first = coordinator.pump(TranscriptionPumpContext())
    assert first is not None
    assert first.job_id == "first"
    assert coordinator.pump(TranscriptionPumpContext()) is None

    coordinator.complete(first.job_id, first.lesson)
    second = coordinator.pump(TranscriptionPumpContext())
    assert second is not None
    assert second.job_id == "second"


def test_fail_releases_queue_for_next_job(tmp_path: Path) -> None:
    coordinator = TranscriptionQueueCoordinator()
    first_audio = tmp_path / "first.wav"
    second_audio = tmp_path / "second.wav"
    first_audio.touch()
    second_audio.touch()
    coordinator.enqueue(lesson("first"), first_audio)
    coordinator.enqueue(lesson("second"), second_audio)

    first = coordinator.pump(TranscriptionPumpContext())
    assert first is not None
    coordinator.fail(first.job_id, "boom")

    second = coordinator.pump(TranscriptionPumpContext())
    assert second is not None
    assert second.job_id == "second"


def test_retry_prepares_lesson_persists_state_and_requeues(tmp_path: Path) -> None:
    written: list[str] = []
    coordinator = TranscriptionQueueCoordinator(
        retry_state_writer=lambda source: written.append(source.lesson_id)
    )
    audio = tmp_path / "lesson.wav"
    audio.touch()
    source = lesson("retry", status=JobStatus.RECORDED)
    coordinator.enqueue(source, audio)
    running = coordinator.pump(TranscriptionPumpContext())
    assert running is not None
    coordinator.fail(running.job_id, "temporary")
    source.transition(JobStatus.FAILED, force=True)

    retried = coordinator.retry(source.lesson_id)

    assert retried.status.value == "waiting"
    assert retried.lesson.status == JobStatus.RECORDED
    assert written == [source.lesson_id]
    submission = coordinator.pump(TranscriptionPumpContext())
    assert submission is not None
    assert submission.job_id == source.lesson_id


def test_retry_rejects_missing_audio_without_mutating_lesson(tmp_path: Path) -> None:
    written: list[str] = []
    coordinator = TranscriptionQueueCoordinator(
        retry_state_writer=lambda source: written.append(source.lesson_id)
    )
    source = lesson("missing", status=JobStatus.RECORDED)
    audio = tmp_path / "missing.wav"
    coordinator.enqueue(source, audio)
    running = coordinator.pump(TranscriptionPumpContext())
    assert running is not None
    coordinator.fail(running.job_id, "temporary")
    source.transition(JobStatus.FAILED, force=True)

    with pytest.raises(TranscriptionAudioMissingError) as captured:
        coordinator.retry(source.lesson_id)

    assert captured.value.path == audio
    assert source.status == JobStatus.FAILED
    assert written == []
    assert coordinator.get(source.lesson_id).status.value == "failed"


def test_restore_history_preserves_persisted_semantics_and_recovers_orphans(
    tmp_path: Path,
) -> None:
    coordinator = TranscriptionQueueCoordinator()
    running_audio = tmp_path / "running.wav"
    orphan_audio = tmp_path / "orphan.wav"
    running_audio.touch()
    orphan_audio.touch()

    running = lesson("running", status=JobStatus.TRANSCRIBING)
    ready = lesson("ready", status=JobStatus.REVIEW_REQUIRED)
    missing = lesson("missing", status=JobStatus.TRANSCRIBING)
    orphan = lesson("orphan", status=JobStatus.RECORDED)
    orphan.source_audio_local = str(orphan_audio)
    lessons = [running, ready, missing, orphan]
    stored = [
        StoredJob("running", str(running_audio), "running"),
        StoredJob("ready", str(tmp_path / "gone.wav"), "ready"),
        StoredJob("missing", str(tmp_path / "missing.wav"), "waiting"),
        StoredJob("ignored", str(running_audio), "waiting"),
    ]

    restored = coordinator.restore_history(lessons, stored)

    assert restored == 3
    snapshot = coordinator.snapshot()
    assert {entry.job_id for entry in snapshot.entries} == {"running", "ready", "orphan"}
    assert coordinator.get("running").status.value == "waiting"
    assert coordinator.get("ready").status.value == "ready"
    assert coordinator.get("orphan").status.value == "waiting"
    assert coordinator.get("missing") is None


def test_snapshot_contains_ui_neutral_queue_state(tmp_path: Path) -> None:
    coordinator = TranscriptionQueueCoordinator()
    waiting_audio = tmp_path / "waiting.wav"
    ready_audio = tmp_path / "ready.wav"
    waiting_audio.touch()
    ready_audio.touch()
    waiting = lesson("waiting")
    ready = lesson("ready")
    coordinator.enqueue(waiting, waiting_audio)
    coordinator.enqueue(ready, ready_audio)
    first = coordinator.pump(TranscriptionPumpContext())
    assert first is not None
    coordinator.complete(first.job_id, first.lesson)

    snapshot = coordinator.snapshot()

    assert snapshot.unfinished_count == 1
    assert snapshot.ready_count == 1
    assert snapshot.visible_count == 2
    assert [(entry.job_id, entry.status) for entry in snapshot.entries] == [
        ("waiting", "ready"),
        ("ready", "waiting"),
    ]
