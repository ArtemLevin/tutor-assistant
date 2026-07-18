from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tutor_assistant.content import (
    ActiveLessonError,
    ContentOperationKind,
    ContentOperationStatus,
    StudentContentService,
    TrashEntry,
    TrashState,
)
from tutor_assistant.domain import JobStatus, Lesson, PublicationInfo, Student
from tutor_assistant.store import LessonStore


def make_lesson(lesson_id: str = "trash-lesson", *, status: JobStatus = JobStatus.DRAFT) -> Lesson:
    return Lesson(
        lesson_id=lesson_id,
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 18),
        topic="Корзина",
        status=status,
        publication=PublicationInfo(
            branch="lesson/published",
            repository_path="students/student/lesson",
            commit="abc123",
            pr_url="https://github.com/example/repo/pull/1",
        ),
    )


def create_with_file(service: StudentContentService, lesson_id: str = "trash-lesson") -> Lesson:
    lesson = service.create_lesson(make_lesson(lesson_id))
    payload = service.workspace / "lessons" / lesson_id / "recording" / "lesson.wav"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"audio-data")
    return lesson


def test_delete_and_restore_move_managed_files_and_preserve_publication(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data", trash_retention_days=7)
    lesson = create_with_file(service)
    source = service.workspace / "lessons" / lesson.lesson_id
    expected_size = sum(path.stat().st_size for path in source.rglob("*") if path.is_file())

    deleted = service.delete_lesson(lesson.lesson_id)
    summary = service.trash_summary()

    assert deleted.size_bytes == expected_size
    assert not source.exists()
    assert (service.workspace / "trash" / "lessons" / lesson.lesson_id).is_dir()
    assert service.list_lessons().total == 0
    assert len(summary.items) == 1
    assert summary.total_size_bytes == expected_size
    assert summary.items[0].lesson.publication == lesson.publication
    assert summary.items[0].entry.state == TrashState.TRASHED

    restored = service.restore_lesson(lesson.lesson_id)

    assert restored.size_bytes == expected_size
    assert source.is_dir()
    assert (source / "recording" / "lesson.wav").read_bytes() == b"audio-data"
    assert service.get_lesson(lesson.lesson_id).lesson.publication == lesson.publication
    assert service.trash_summary().items == []


@pytest.mark.parametrize("status", [JobStatus.RECORDING, JobStatus.TRANSCRIBING])
def test_active_lesson_cannot_be_deleted(tmp_path: Path, status: JobStatus) -> None:
    service = StudentContentService(tmp_path / "data")
    lesson = service.create_lesson(make_lesson(status=status))

    with pytest.raises(ActiveLessonError):
        service.delete_lesson(lesson.lesson_id)

    assert service.get_lesson(lesson.lesson_id).lesson.status == status
    assert (service.workspace / "lessons" / lesson.lesson_id).is_dir()
    assert service.trash_summary().items == []


def test_lesson_in_active_transcription_queue_cannot_be_deleted(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    lesson = create_with_file(service)
    LessonStore(service.repository.path).save_transcription_job(
        lesson.lesson_id,
        str(service.workspace / "lessons" / lesson.lesson_id / "recording" / "lesson.wav"),
        "waiting",
    )

    with pytest.raises(ActiveLessonError, match="очереди"):
        service.delete_lesson(lesson.lesson_id)

    assert service.get_lesson(lesson.lesson_id).lesson.lesson_id == lesson.lesson_id


def test_permanent_delete_removes_local_records_and_reports_freed_space(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    lesson = create_with_file(service)
    service.save_transcript(lesson.lesson_id, "Версия для удаления")
    service.save_transcript_draft(
        lesson.lesson_id,
        "Черновик",
        base_revision_number=1,
    )
    deleted = service.delete_lesson(lesson.lesson_id)

    purged = service.permanently_delete_lesson(lesson.lesson_id)
    operations = service.repository.list_operations()

    assert purged.size_bytes == deleted.size_bytes
    assert service.repository.get_lesson(lesson.lesson_id, include_deleted=True) is None
    assert not (service.workspace / "trash" / "lessons" / lesson.lesson_id).exists()
    assert not (service.workspace / ".trash-purge").exists()
    assert [item.operation for item in operations[:2]] == [
        ContentOperationKind.PURGE,
        ContentOperationKind.DELETE,
    ]
    assert all(item.status == ContentOperationStatus.COMPLETED for item in operations[:2])


@pytest.mark.parametrize("move_before_restart", [False, True])
def test_restart_recovers_crash_during_move_to_trash(
    tmp_path: Path,
    move_before_restart: bool,
) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    lesson = create_with_file(service)
    now = datetime.now(UTC)
    entry = TrashEntry(
        lesson_id=lesson.lesson_id,
        original_relative_path=f"lessons/{lesson.lesson_id}",
        trash_relative_path=f"trash/lessons/{lesson.lesson_id}",
        size_bytes=service._directory_size(workspace / "lessons" / lesson.lesson_id),
        state=TrashState.MOVING,
        deleted_at=now,
        purge_after=now + timedelta(days=30),
    )
    service.repository.begin_trash(entry, "crashed-delete")
    if move_before_restart:
        destination = workspace / entry.trash_relative_path
        destination.parent.mkdir(parents=True)
        (workspace / entry.original_relative_path).replace(destination)

    recovered = StudentContentService(workspace)

    assert not (workspace / entry.original_relative_path).exists()
    assert (workspace / entry.trash_relative_path).is_dir()
    assert recovered.trash_summary().items[0].entry.state == TrashState.TRASHED
    operation = recovered.repository.list_operations()[0]
    assert operation.status == ContentOperationStatus.COMPLETED


@pytest.mark.parametrize("move_before_restart", [False, True])
def test_restart_recovers_crash_during_restore(
    tmp_path: Path,
    move_before_restart: bool,
) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    lesson = create_with_file(service)
    service.delete_lesson(lesson.lesson_id)
    entry = service.repository.begin_restore(
        lesson.lesson_id,
        "crashed-restore",
        datetime.now(UTC),
    )
    if move_before_restart:
        source = workspace / entry.trash_relative_path
        destination = workspace / entry.original_relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)

    recovered = StudentContentService(workspace)

    assert (workspace / entry.original_relative_path).is_dir()
    assert not (workspace / entry.trash_relative_path).exists()
    assert recovered.get_lesson(lesson.lesson_id).lesson.lesson_id == lesson.lesson_id
    assert recovered.trash_summary().items == []
    operation = recovered.repository.list_operations()[0]
    assert operation.operation == ContentOperationKind.RESTORE
    assert operation.status == ContentOperationStatus.COMPLETED


@pytest.mark.parametrize("database_purged_before_restart", [False, True])
def test_restart_recovers_crash_during_permanent_delete(
    tmp_path: Path,
    database_purged_before_restart: bool,
) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    lesson = create_with_file(service)
    service.delete_lesson(lesson.lesson_id)
    operation_id = "crashed-purge"
    staging_relative = ".trash-purge/crashed-purge"
    entry = service.repository.begin_purge(
        lesson.lesson_id,
        operation_id,
        staging_relative,
        datetime.now(UTC),
    )
    source = workspace / entry.trash_relative_path
    staging = workspace / staging_relative
    staging.parent.mkdir(parents=True, exist_ok=True)
    source.replace(staging)
    if database_purged_before_restart:
        service.repository.complete_purge_database(lesson.lesson_id, operation_id)

    recovered = StudentContentService(workspace)

    assert recovered.repository.get_lesson(lesson.lesson_id, include_deleted=True) is None
    assert not source.exists()
    assert not staging.exists()
    assert not staging.parent.exists()
    operation = recovered.repository.list_operations()[0]
    assert operation.operation == ContentOperationKind.PURGE
    assert operation.status == ContentOperationStatus.COMPLETED


def test_failed_move_rolls_back_database_and_keeps_source(tmp_path: Path, monkeypatch) -> None:
    service = StudentContentService(tmp_path / "data")
    lesson = create_with_file(service)
    source = service.workspace / "lessons" / lesson.lesson_id

    def fail_replace(_self: Path, _target: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(PermissionError, match="locked"):
        service.delete_lesson(lesson.lesson_id)

    assert source.is_dir()
    assert service.get_lesson(lesson.lesson_id).lesson.lesson_id == lesson.lesson_id
    assert service.trash_summary().items == []
    assert service.repository.list_operations()[0].status == ContentOperationStatus.FAILED


def test_retention_purges_only_expired_items(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data", trash_retention_days=1)
    lesson = create_with_file(service)
    service.delete_lesson(lesson.lesson_id)
    deleted_at = service.trash_summary().items[0].entry.deleted_at

    assert service.purge_expired_trash(now=deleted_at + timedelta(hours=23)) == []
    service.set_trash_retention_days(2)
    assert service.trash_summary().items[0].entry.purge_after == deleted_at + timedelta(days=2)
    assert service.purge_expired_trash(now=deleted_at + timedelta(days=1, seconds=1)) == []
    results = service.purge_expired_trash(now=deleted_at + timedelta(days=2, seconds=1))

    assert [result.lesson_id for result in results] == [lesson.lesson_id]
    assert service.trash_summary().items == []
