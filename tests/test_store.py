import hashlib
from datetime import date

import pytest

from tutor_assistant.domain import Lesson, Student
from tutor_assistant.store import AutomaticPublicationJobConflictError, LessonStore


def _insert_transcript_revision(
    store: LessonStore,
    lesson_id: str,
    revision_number: int,
    content: str,
    *,
    created_by: str = "automatic-transcription",
) -> str:
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with store.connect() as db:
        db.execute(
            """
            INSERT INTO transcript_revisions (
                lesson_id, revision_number, relative_path, content,
                content_sha256, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                lesson_id,
                revision_number,
                f"lessons/{lesson_id}/transcript/transcript_verified.txt",
                content,
                content_sha256,
                created_by,
            ),
        )
    return content_sha256


def test_store_creates_lesson_but_rejects_legacy_updates(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 7, 12),
        topic="Резонанс",
    )
    store.save(lesson)
    lesson.topic = "Механический резонанс"
    with pytest.raises(RuntimeError, match="StudentContentService"):
        store.save(lesson)
    assert store.get(lesson.lesson_id).topic == "Резонанс"
    assert len(store.list()) == 1


def test_store_uses_wal_and_persists_transcription_job(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 7, 12),
        topic="Волны",
    )
    store.save(lesson)
    store.save_transcription_job(lesson.lesson_id, "lesson.wav", "waiting")

    with store.connect() as db:
        journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]

    assert journal_mode == "wal"
    assert store.list_transcription_jobs()[0].lesson_id == lesson.lesson_id


def test_store_persists_immutable_automatic_publication_intent(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)

    first = store.ensure_automatic_publication_job(
        lesson.lesson_id,
        1,
        "a" * 64,
        "students/student/transcript/04.10.26.txt",
    )
    second = store.ensure_automatic_publication_job(
        lesson.lesson_id,
        1,
        "a" * 64,
        "students/student/transcript/04.10.26.txt",
    )
    running = store.update_automatic_publication_job(
        lesson.lesson_id,
        "running",
        increment_attempts=True,
    )

    assert first == second
    assert running.status == "running"
    assert running.attempts == 1
    assert store.list_automatic_publication_jobs() == [running]


def test_store_rejects_publication_intent_payload_change(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    store.ensure_automatic_publication_job(
        lesson.lesson_id,
        1,
        "a" * 64,
        "students/student/transcript/04.10.26.txt",
    )

    with pytest.raises(AutomaticPublicationJobConflictError):
        store.ensure_automatic_publication_job(
            lesson.lesson_id,
            2,
            "b" * 64,
            "students/student/transcript/04.10.26.txt",
        )


def test_store_repairs_unpublished_automatic_publication_intent(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    empty_sha = _insert_transcript_revision(store, lesson.lesson_id, 1, "\n")
    valid_sha = _insert_transcript_revision(store, lesson.lesson_id, 2, "valid transcript\n")
    store.ensure_automatic_publication_job(lesson.lesson_id, 1, empty_sha, path)
    store.update_automatic_publication_job(
        lesson.lesson_id,
        "blocked",
        error="legacy empty transcript",
        increment_attempts=True,
    )

    repaired = store.repair_automatic_publication_job(
        lesson.lesson_id,
        expected_revision_number=1,
        expected_content_sha256=empty_sha,
        expected_repository_path=path,
        revision_number=2,
        content_sha256=valid_sha,
        repository_path=path,
    )

    assert repaired.revision_number == 2
    assert repaired.content_sha256 == valid_sha
    assert repaired.status == "waiting"
    assert repaired.attempts == 0
    assert repaired.error is None
    assert repaired.next_attempt_at is None


def test_store_repair_rejects_meaningful_pinned_revision(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    old_sha = _insert_transcript_revision(store, lesson.lesson_id, 1, "already valid\n")
    new_sha = _insert_transcript_revision(store, lesson.lesson_id, 2, "replacement\n")
    store.ensure_automatic_publication_job(lesson.lesson_id, 1, old_sha, path)

    with pytest.raises(AutomaticPublicationJobConflictError, match="empty"):
        store.repair_automatic_publication_job(
            lesson.lesson_id,
            expected_revision_number=1,
            expected_content_sha256=old_sha,
            expected_repository_path=path,
            revision_number=2,
            content_sha256=new_sha,
            repository_path=path,
        )

    stored = store.get_automatic_publication_job(lesson.lesson_id)
    assert stored is not None
    assert stored.revision_number == 1
    assert stored.content_sha256 == old_sha


def test_store_repair_rejects_nonautomatic_empty_pinned_revision(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    old_sha = _insert_transcript_revision(
        store,
        lesson.lesson_id,
        1,
        "\n",
        created_by="teacher-review",
    )
    new_sha = _insert_transcript_revision(store, lesson.lesson_id, 2, "replacement\n")
    store.ensure_automatic_publication_job(lesson.lesson_id, 1, old_sha, path)

    with pytest.raises(AutomaticPublicationJobConflictError, match="automatic"):
        store.repair_automatic_publication_job(
            lesson.lesson_id,
            expected_revision_number=1,
            expected_content_sha256=old_sha,
            expected_repository_path=path,
            revision_number=2,
            content_sha256=new_sha,
            repository_path=path,
        )

    stored = store.get_automatic_publication_job(lesson.lesson_id)
    assert stored is not None
    assert stored.revision_number == 1
    assert stored.content_sha256 == old_sha


def test_store_repair_rejects_corrupted_revision_sha(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    old_sha = _insert_transcript_revision(store, lesson.lesson_id, 1, "\n")
    new_sha = _insert_transcript_revision(store, lesson.lesson_id, 2, "replacement\n")
    store.ensure_automatic_publication_job(lesson.lesson_id, 1, old_sha, path)
    with store.connect() as db:
        db.execute(
            """
            UPDATE transcript_revisions
            SET content='corrupted transcript'
            WHERE lesson_id=? AND revision_number=1
            """,
            (lesson.lesson_id,),
        )

    with pytest.raises(AutomaticPublicationJobConflictError, match="SHA-256"):
        store.repair_automatic_publication_job(
            lesson.lesson_id,
            expected_revision_number=1,
            expected_content_sha256=old_sha,
            expected_repository_path=path,
            revision_number=2,
            content_sha256=new_sha,
            repository_path=path,
        )

    stored = store.get_automatic_publication_job(lesson.lesson_id)
    assert stored is not None
    assert stored.revision_number == 1
    assert stored.content_sha256 == old_sha


def test_store_repair_rejects_nonautomatic_or_empty_replacement(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    old_sha = _insert_transcript_revision(store, lesson.lesson_id, 1, "\n")
    teacher_sha = _insert_transcript_revision(
        store,
        lesson.lesson_id,
        2,
        "teacher text\n",
        created_by="teacher-review",
    )
    store.ensure_automatic_publication_job(lesson.lesson_id, 1, old_sha, path)

    with pytest.raises(AutomaticPublicationJobConflictError, match="automatic"):
        store.repair_automatic_publication_job(
            lesson.lesson_id,
            expected_revision_number=1,
            expected_content_sha256=old_sha,
            expected_repository_path=path,
            revision_number=2,
            content_sha256=teacher_sha,
            repository_path=path,
        )

    empty_sha = _insert_transcript_revision(store, lesson.lesson_id, 3, "...\n")
    with pytest.raises(AutomaticPublicationJobConflictError, match="meaningful"):
        store.repair_automatic_publication_job(
            lesson.lesson_id,
            expected_revision_number=1,
            expected_content_sha256=old_sha,
            expected_repository_path=path,
            revision_number=3,
            content_sha256=empty_sha,
            repository_path=path,
        )


def test_store_quarantines_only_stale_running_empty_intent(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    empty_sha = _insert_transcript_revision(store, lesson.lesson_id, 1, "\n")
    store.ensure_automatic_publication_job(lesson.lesson_id, 1, empty_sha, path)
    store.update_automatic_publication_job(
        lesson.lesson_id,
        "running",
        increment_attempts=True,
    )

    quarantined = store.quarantine_stale_empty_automatic_publication_job(
        lesson.lesson_id,
        expected_revision_number=1,
        expected_content_sha256=empty_sha,
        expected_repository_path=path,
        error="empty transcript",
    )

    assert quarantined.status == "blocked"
    assert quarantined.attempts == 1
    assert quarantined.error == "empty transcript"


def test_store_rejects_quarantine_for_meaningful_running_intent(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    valid_sha = _insert_transcript_revision(store, lesson.lesson_id, 1, "valid\n")
    store.ensure_automatic_publication_job(lesson.lesson_id, 1, valid_sha, path)
    store.update_automatic_publication_job(lesson.lesson_id, "running")

    with pytest.raises(AutomaticPublicationJobConflictError, match="empty"):
        store.quarantine_stale_empty_automatic_publication_job(
            lesson.lesson_id,
            expected_revision_number=1,
            expected_content_sha256=valid_sha,
            expected_repository_path=path,
            error="empty transcript",
        )


@pytest.mark.parametrize("terminal_status", ["running", "published"])
def test_store_inactive_reconciliation_update_does_not_steal_terminal_owner(
    tmp_path,
    terminal_status,
) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    content_sha256 = "a" * 64
    store.ensure_automatic_publication_job(
        lesson.lesson_id,
        1,
        content_sha256,
        path,
    )
    store.update_automatic_publication_job(lesson.lesson_id, terminal_status)

    updated = store.update_inactive_automatic_publication_job(
        lesson.lesson_id,
        expected_revision_number=1,
        expected_content_sha256=content_sha256,
        expected_repository_path=path,
        status="conflict",
        error="stale reconciliation",
    )

    stored = store.get_automatic_publication_job(lesson.lesson_id)
    assert updated is None
    assert stored is not None
    assert stored.status == terminal_status
    assert stored.error is None


@pytest.mark.parametrize("status", ["running", "published"])
def test_store_rejects_repair_after_publication_starts(tmp_path, status) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    store.ensure_automatic_publication_job(lesson.lesson_id, 1, "a" * 64, path)
    store.update_automatic_publication_job(lesson.lesson_id, status)

    with pytest.raises(AutomaticPublicationJobConflictError):
        store.repair_automatic_publication_job(
            lesson.lesson_id,
            expected_revision_number=1,
            expected_content_sha256="a" * 64,
            expected_repository_path=path,
            revision_number=2,
            content_sha256="b" * 64,
            repository_path=path,
        )


def test_store_repair_uses_compare_and_swap_for_immutable_payload(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )
    store.save(lesson)
    path = "students/student/transcript/04.10.26.txt"
    store.ensure_automatic_publication_job(lesson.lesson_id, 1, "a" * 64, path)

    with pytest.raises(AutomaticPublicationJobConflictError):
        store.repair_automatic_publication_job(
            lesson.lesson_id,
            expected_revision_number=1,
            expected_content_sha256="c" * 64,
            expected_repository_path=path,
            revision_number=2,
            content_sha256="b" * 64,
            repository_path=path,
        )

    stored = store.get_automatic_publication_job(lesson.lesson_id)
    assert stored is not None
    assert stored.revision_number == 1
    assert stored.content_sha256 == "a" * 64
