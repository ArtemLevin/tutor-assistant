from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from tutor_assistant.application.content_cleanup import BulkCleanupRuntime, cleanup_runtime_blockers
from tutor_assistant.content import (
    ActiveLessonError,
    ContentBusyError,
    ContentConflictError,
    ContentPathError,
    StudentContentService,
)
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.publication.journal import PublicationOperationStore
from tutor_assistant.publication_queue import AutomaticPublicationQueue
from tutor_assistant.store import LessonStore
from tutor_assistant.transcription_queue import TranscriptionQueue


def lesson(identifier: str, *, status: JobStatus = JobStatus.DRAFT) -> Lesson:
    return Lesson(
        lesson_id=identifier,
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 18),
        topic="Тестовая очистка",
        status=status,
    )


def stored(service: StudentContentService, identifier: str) -> Path:
    service.create_lesson(lesson(identifier))
    audio = service.workspace / "lessons" / identifier / "recording" / "lesson.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"content-audio")
    return audio


def test_bulk_cleanup_removes_all_pages_and_trash_but_preserves_backups(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    for index in range(55):
        stored(service, f"bulk-{index:03}")
    service.delete_lesson("bulk-000")
    service.delete_lesson("bulk-001")
    service.save_transcript("bulk-002", "Тестовый транскрипт")
    backup = service.create_database_backup(reason="test")
    before = service.preview_bulk_cleanup()

    assert before.total_lessons_count == 55
    assert before.trashed_lessons_count == 2
    assert before.estimated_total_bytes >= 55 * len(b"content-audio")
    assert not before.blockers

    events = []
    result = service.run_bulk_cleanup(before.fingerprint, progress=events.append)

    assert result.state == "completed"
    assert result.purged == 55
    assert len(events) == 55
    assert service.repository.list_lesson_index_states() == []
    assert service.trash_summary().items == []
    assert backup.path.is_file()
    assert service.repository.database_integrity_status()[0]
    assert service.run_bulk_cleanup(service.preview_bulk_cleanup().fingerprint).state == "completed"


def test_bulk_cleanup_detaches_crm_occurrences_and_preserves_financial_fields(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    stored(service, "crm-linked")
    with service.repository.connect() as db:
        db.execute(
            "CREATE TABLE crm_lesson_occurrences "
            "(id INTEGER PRIMARY KEY, lesson_id TEXT, status TEXT, rate_cents INTEGER, paid INTEGER)"
        )
        db.execute(
            "INSERT INTO crm_lesson_occurrences VALUES (1, 'crm-linked', 'completed', 2500, 1)"
        )
    result = service.run_bulk_cleanup(service.preview_bulk_cleanup().fingerprint)
    assert result.state == "completed"
    with service.repository.connect() as db:
        row = db.execute(
            "SELECT lesson_id, status, rate_cents, paid FROM crm_lesson_occurrences WHERE id=1"
        ).fetchone()
    assert tuple(row) == (None, "completed", 2500, 1)


def test_bulk_cleanup_discards_waiting_transcription_but_normal_delete_stays_blocked(
    tmp_path: Path,
) -> None:
    service = StudentContentService(tmp_path / "data")
    audio = stored(service, "waiting-job")
    LessonStore(service.repository.path).save_transcription_job(
        "waiting-job", str(audio), "waiting"
    )

    with pytest.raises(ActiveLessonError, match="активной очереди"):
        service.delete_lesson("waiting-job")
    preview = service.preview_bulk_cleanup()
    assert preview.total_lessons_count == 1
    assert not preview.blockers

    result = service.run_bulk_cleanup(preview.fingerprint)

    assert result.state == "completed"
    assert result.purged == 1
    assert service.repository.list_lesson_index_states() == []
    assert LessonStore(service.repository.path).list_transcription_jobs() == []
    assert not audio.exists()


def test_failed_bulk_move_preserves_waiting_transcription_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = StudentContentService(tmp_path / "data")
    audio = stored(service, "cannot-move")
    LessonStore(service.repository.path).save_transcription_job(
        "cannot-move", str(audio), "waiting"
    )
    original_replace = Path.replace

    def reject_rename(source: Path, target: Path) -> Path:
        if source == service.workspace / "lessons" / "cannot-move":
            raise PermissionError("simulated Windows file lock")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", reject_rename)
    result = service.run_bulk_cleanup(service.preview_bulk_cleanup().fingerprint)

    assert result.state == "partial"
    assert result.purged == 0
    assert result.remaining_count == 1
    assert audio.exists()
    jobs = LessonStore(service.repository.path).list_transcription_jobs()
    assert len(jobs) == 1
    assert jobs[0].status == "waiting"
    remaining = service.repository.get_lesson("cannot-move")
    assert remaining is not None


def test_symlinked_publication_journal_is_rejected(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    audio = stored(service, "test-journal-path")
    external = PublicationOperationStore(tmp_path / "external-publication.sqlite3")
    link = service.workspace / "publication.sqlite3"
    try:
        link.symlink_to(external.path)
    except (OSError, NotImplementedError):
        pytest.skip("OS does not allow creating symlinks")
    preview = service.preview_bulk_cleanup()
    assert any("журнала публикаций" in item for item in preview.blockers)
    with pytest.raises(ContentBusyError):
        service.run_bulk_cleanup(preview.fingerprint)
    assert audio.is_file()
    assert external.path.is_file()


def test_recoverable_recording_chunks_block_cleanup(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    audio = stored(service, "incomplete")
    recording = audio.parent
    (recording / "session.json").write_text('{"status": "failed_to_stop"}')
    chunks = recording / "chunks" / "microphone"
    chunks.mkdir(parents=True)
    (chunks / "001.wav").write_bytes(b"recoverable")
    preview = service.preview_bulk_cleanup()
    assert any("восстановления" in item for item in preview.blockers)
    with pytest.raises(ContentBusyError):
        service.run_bulk_cleanup(preview.fingerprint)
    assert audio.is_file()


def test_bulk_cleanup_rejects_stale_preview_before_any_mutation(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    first = stored(service, "first")
    stale = service.preview_bulk_cleanup().fingerprint
    stored(service, "second")
    with pytest.raises(ContentConflictError, match="изменился"):
        service.run_bulk_cleanup(stale)
    assert first.is_file()
    assert service.list_lessons().total == 2


def test_bulk_cleanup_rejects_recording_and_cross_process_leases(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    first = StudentContentService(workspace)
    first.create_lesson(lesson("active", status=JobStatus.RECORDING))
    preview = first.preview_bulk_cleanup()
    assert preview.blockers
    with pytest.raises(ContentBusyError):
        first.run_bulk_cleanup(preview.fingerprint)
    assert first.repository.get_lesson("active") is not None

    with first.activity("recording", lesson_id="active"):
        other = StudentContentService(workspace)
        assert other.preview_bulk_cleanup().blockers
        with pytest.raises(ContentBusyError):
            other.run_bulk_cleanup(other.preview_bulk_cleanup().fingerprint)


def _journal(store: PublicationOperationStore, lesson_id: str) -> str:
    operation = store.begin(
        lesson_id=lesson_id,
        repository_full_name="test/private",
        remote_name="origin",
        remote_url_sha256="a" * 64,
        branch="main",
        repository_path="transcript/test.txt",
        content_sha256="b" * 64,
        content_size_bytes=42,
        expected_remote_sha="c" * 40,
    )
    return operation.id


def test_bulk_cleanup_blocks_incomplete_remote_publication(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    audio = stored(service, "has-publication")
    journal = PublicationOperationStore(service.workspace / "publication.sqlite3")
    _journal(journal, "has-publication")
    preview = service.preview_bulk_cleanup()
    assert any("публикация" in item.casefold() for item in preview.blockers)
    with pytest.raises(ContentBusyError):
        service.run_bulk_cleanup(preview.fingerprint)
    assert audio.is_file()


def test_bulk_cleanup_removes_only_terminal_publication_records(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    stored(service, "published")
    journal = PublicationOperationStore(service.workspace / "publication.sqlite3")
    identifier = _journal(journal, "published")
    journal.mark_failed(identifier, error_code="test", details="synthetic")
    preview = service.preview_bulk_cleanup()
    assert not preview.blockers
    assert service.run_bulk_cleanup(preview.fingerprint).state == "completed"
    with journal._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM publication_operations").fetchone()[0] == 0


def test_cleanup_temporary_files_never_unlinks_files_through_linked_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = StudentContentService(tmp_path / "data")
    external = tmp_path / "outside"
    external.mkdir()
    protected = external / "private.tmp"
    protected.write_text("must-survive", encoding="utf-8")
    link = service.workspace / "lessons" / "orphan-junction"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("OS does not allow creating directory links")

    # Inject the path found by an OS/Path.rglob implementation that follows links.
    monkeypatch.setattr(service, "_temporary_candidates", lambda **_kwargs: [link / "private.tmp"])
    result = service.cleanup_temporary_files()

    assert protected.read_text(encoding="utf-8") == "must-survive"
    assert result.removed_paths == []
    assert result.errors


def test_bulk_cleanup_rejects_trash_path_redirection_into_backups(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    stored(service, "misdirected")
    service.delete_lesson("misdirected")
    protected = service.workspace / "backups" / "keep.txt"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("do-not-remove", encoding="utf-8")

    with service.repository.connect() as db:
        db.execute(
            "UPDATE content_trash SET trash_relative_path='backups' WHERE lesson_id=?",
            ("misdirected",),
        )

    preview = service.preview_bulk_cleanup()
    with pytest.raises(ContentPathError, match="Каталог корзины"):
        service.run_bulk_cleanup(preview.fingerprint)
    assert protected.read_text(encoding="utf-8") == "do-not-remove"
    assert service.repository.get_lesson("misdirected", include_deleted=True) is not None


def test_bulk_cleanup_path_escape_fails_closed(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    original = stored(service, "linked")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "must-stay.txt"
    sentinel.write_text("preserve")
    original.parent.parent.rename(service.workspace / "moved-out")
    link = service.workspace / "lessons" / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("OS does not allow creating symlinks")
    with pytest.raises(ContentPathError):
        service.run_bulk_cleanup(service.preview_bulk_cleanup().fingerprint)
    assert sentinel.read_text() == "preserve"


def test_bulk_cleanup_reports_partial_on_sqlite_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = StudentContentService(tmp_path / "data")
    stored(service, "failed-db")
    stored(service, "healthy-db")
    original = service.stage_lesson_purge

    def fail_one(lesson_id: str):
        if lesson_id == "failed-db":
            raise sqlite3.OperationalError("simulated SQLite I/O failure")
        return original(lesson_id)

    monkeypatch.setattr(service, "stage_lesson_purge", fail_one)
    result = service.run_bulk_cleanup(service.preview_bulk_cleanup().fingerprint)

    assert result.state == "partial"
    assert result.purged == 1
    assert result.remaining_count == 1
    assert len(result.failures) == 1
    assert result.failures[0].lesson_id == "failed-db"
    assert service.repository.database_integrity_status()[0]


def test_bulk_cleanup_reports_partial_on_io_failure_and_recovers_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    stored(service, "fails")
    stored(service, "succeeds")
    original = service.finalize_staged_purge

    def fail_once(operation_id: str, staging: Path) -> None:
        if staging.name and (staging / "recording" / "lesson.wav").exists():
            if staging.name != "":
                monkeypatch.setattr(service, "finalize_staged_purge", original)
                raise PermissionError("simulated Windows lock")
        original(operation_id, staging)

    monkeypatch.setattr(service, "finalize_staged_purge", fail_once)
    result = service.run_bulk_cleanup(service.preview_bulk_cleanup().fingerprint)
    assert result.state == "partial"
    assert len(result.failures) == 1
    assert service.repository.database_integrity_status()[0]
    restarted = StudentContentService(workspace)
    assert not (workspace / ".trash-purge").exists()
    assert restarted.repository.list_lesson_index_states() == []


def test_runtime_policy_and_in_memory_queue_cleanup() -> None:
    assert cleanup_runtime_blockers(BulkCleanupRuntime()) == ()
    assert cleanup_runtime_blockers(
        BulkCleanupRuntime(recording_active=True, publication_active=True)
    )
    item = lesson("queue-item")
    publication = AutomaticPublicationQueue()
    publication.enqueue(
        item, revision_number=1, content_sha256="a" * 64, repository_path="test.txt"
    )
    publication.discard_after_confirmed_cleanup(item.lesson_id)
    assert publication.jobs == ()
    publication.enqueue(
        item, revision_number=1, content_sha256="a" * 64, repository_path="test.txt"
    )
    assert publication.start_next() is not None
    with pytest.raises(ValueError):
        publication.discard_after_confirmed_cleanup(item.lesson_id)
    transcriptions = TranscriptionQueue()
    transcriptions.enqueue(item, Path("audio.wav"))
    transcriptions.discard_after_confirmed_cleanup(item.lesson_id)
    assert transcriptions.jobs == ()
