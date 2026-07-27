from __future__ import annotations

from pathlib import Path

from tutor_assistant.content import StudentContentRepository


def test_migration_10_creates_privacy_audit_tables(tmp_path: Path) -> None:
    repository = StudentContentRepository(tmp_path / "content.sqlite3")

    assert repository.applied_migrations()[-1] == (10, "cloud_processing_privacy")
    with repository.connect() as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        consent_columns = {
            row[1] for row in db.execute("PRAGMA table_info(cloud_processing_consents)")
        }
        event_columns = {
            row[1] for row in db.execute("PRAGMA table_info(cloud_request_events)")
        }

    assert {"cloud_processing_consents", "cloud_request_events"} <= tables
    assert "request_fingerprint" in consent_columns
    assert {"event", "response_sha256", "error_code"} <= event_columns
    assert "normalized_text" not in consent_columns
    assert "prompt" not in event_columns


def test_migration_10_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "content.sqlite3"
    StudentContentRepository(path)
    repository = StudentContentRepository(path)

    with repository.connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=10"
        ).fetchone()[0] == 1
