from __future__ import annotations

from pathlib import Path

from tutor_assistant.content import StudentContentRepository


def test_migration_11_creates_automatic_publication_queue(tmp_path: Path) -> None:
    repository = StudentContentRepository(tmp_path / "content.sqlite3")

    assert (11, "automatic_publication_jobs") in repository.applied_migrations()
    with repository.connect() as db:
        columns = {
            row[1]
            for row in db.execute(
                "PRAGMA table_info(automatic_publication_jobs)"
            ).fetchall()
        }
        indexes = {
            row[1]
            for row in db.execute(
                "PRAGMA index_list(automatic_publication_jobs)"
            ).fetchall()
        }

    assert {
        "lesson_id",
        "revision_number",
        "content_sha256",
        "repository_path",
        "status",
        "attempts",
        "error",
        "next_attempt_at",
        "updated_at",
    } <= columns
    assert "automatic_publication_jobs_status_due" in indexes


def test_migration_11_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "content.sqlite3"
    StudentContentRepository(path)
    repository = StudentContentRepository(path)

    with repository.connect() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=11"
        ).fetchone()[0]

    assert count == 1
