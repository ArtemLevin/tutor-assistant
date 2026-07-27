
from __future__ import annotations

import sqlite3

from tutor_assistant.content.migrations import apply_migrations


def test_resumable_normalization_migration_is_idempotent(tmp_path) -> None:
    path = tmp_path / "content.sqlite3"
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    apply_migrations(db)
    apply_migrations(db)

    versions = {int(row[0]) for row in db.execute("SELECT version FROM schema_migrations")}
    assert 9 in versions
    assert 10 in versions
    columns = {str(row[1]) for row in db.execute("PRAGMA table_info(normalization_runs)")}
    assert {"provider", "resume_count", "last_resumed_at"} <= columns
    chunk_columns = {str(row[1]) for row in db.execute("PRAGMA table_info(normalization_chunks)")}
    assert {"run_id", "chunk_index", "chunk_sha256", "quality_json"} <= chunk_columns
    db.close()
