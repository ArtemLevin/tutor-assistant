from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def _column_names(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(db: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split(maxsplit=1)[0]
    if name not in _column_names(db, table):
        db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _create_core_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS lessons (
            lesson_id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            lesson_date TEXT NOT NULL,
            topic TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS transcription_jobs (
            lesson_id TEXT PRIMARY KEY,
            audio_path TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(lesson_id) REFERENCES lessons(lesson_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _content_domain(db: sqlite3.Connection) -> None:
    _add_column(db, "lessons", "subject TEXT NOT NULL DEFAULT ''")
    _add_column(db, "lessons", "created_at TEXT NOT NULL DEFAULT ''")
    _add_column(db, "lessons", "deleted_at TEXT")
    rows = db.execute("SELECT lesson_id, payload, subject, created_at FROM lessons").fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"] if isinstance(row, sqlite3.Row) else row[1])
        except (json.JSONDecodeError, TypeError):
            continue
        lesson_id = row["lesson_id"] if isinstance(row, sqlite3.Row) else row[0]
        subject = row["subject"] if isinstance(row, sqlite3.Row) else row[2]
        created_at = row["created_at"] if isinstance(row, sqlite3.Row) else row[3]
        db.execute(
            """
            UPDATE lessons
            SET subject=?, created_at=?
            WHERE lesson_id=?
            """,
            (
                subject or str(payload.get("subject", "")),
                created_at or str(payload.get("created_at", payload.get("updated_at", ""))),
                lesson_id,
            ),
        )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS lesson_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lesson_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
            size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(size_bytes >= 0),
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            FOREIGN KEY(lesson_id) REFERENCES lessons(lesson_id),
            UNIQUE(lesson_id, relative_path)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS transcript_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lesson_id TEXT NOT NULL,
            revision_number INTEGER NOT NULL CHECK(revision_number > 0),
            relative_path TEXT NOT NULL,
            content TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'teacher',
            created_at TEXT NOT NULL,
            deleted_at TEXT,
            FOREIGN KEY(lesson_id) REFERENCES lessons(lesson_id),
            UNIQUE(lesson_id, revision_number)
        )
        """
    )


def _content_indexes(db: sqlite3.Connection) -> None:
    statements = (
        "CREATE INDEX IF NOT EXISTS lessons_student_date ON lessons(student_id, lesson_date DESC)",
        "CREATE INDEX IF NOT EXISTS lessons_status_updated ON lessons(status, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS lessons_subject_date ON lessons(subject, lesson_date DESC)",
        "CREATE INDEX IF NOT EXISTS lessons_deleted_at ON lessons(deleted_at)",
        "CREATE INDEX IF NOT EXISTS lesson_assets_lesson_kind ON lesson_assets(lesson_id, kind, deleted_at)",
        "CREATE INDEX IF NOT EXISTS transcript_revisions_lesson_revision "
        "ON transcript_revisions(lesson_id, revision_number DESC, deleted_at)",
    )
    for statement in statements:
        db.execute(statement)


def _content_editing(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS transcript_drafts (
            lesson_id TEXT PRIMARY KEY,
            base_revision_number INTEGER,
            content TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(lesson_id) REFERENCES lessons(lesson_id)
        )
        """
    )


def _content_trash(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS content_trash (
            lesson_id TEXT PRIMARY KEY,
            original_relative_path TEXT NOT NULL,
            trash_relative_path TEXT NOT NULL,
            staging_relative_path TEXT,
            size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(size_bytes >= 0),
            state TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            purge_after TEXT NOT NULL,
            FOREIGN KEY(lesson_id) REFERENCES lessons(lesson_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS content_operations (
            id TEXT PRIMARY KEY,
            lesson_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            status TEXT NOT NULL,
            source_relative_path TEXT,
            destination_relative_path TEXT,
            size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(size_bytes >= 0),
            details TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS content_trash_purge_after ON content_trash(state, purge_after)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS content_operations_lesson_created "
        "ON content_operations(lesson_id, created_at DESC)"
    )


def _content_hardening(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS content_capabilities (
            name TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL CHECK(enabled IN (0, 1))
        )
        """
    )
    try:
        db.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS lesson_search USING fts5(
                lesson_id UNINDEXED,
                metadata,
                transcript,
                tokenize='unicode61'
            )
            """
        )
        db.execute("DELETE FROM lesson_search")
        db.execute(
            """
            INSERT INTO lesson_search (lesson_id, metadata, transcript)
            SELECT l.lesson_id, l.payload,
                   COALESCE((
                       SELECT r.content FROM transcript_revisions r
                       WHERE r.lesson_id=l.lesson_id AND r.deleted_at IS NULL
                       ORDER BY r.revision_number DESC LIMIT 1
                   ), '')
            FROM lessons l
            """
        )
        fts_enabled = 1
    except sqlite3.OperationalError as exc:
        if "fts5" not in str(exc).casefold():
            raise
        fts_enabled = 0
    db.execute(
        """
        INSERT INTO content_capabilities (name, enabled) VALUES ('fts5', ?)
        ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled
        """,
        (fts_enabled,),
    )


def _content_write_consistency(db: sqlite3.Connection) -> None:
    _add_column(db, "lessons", "row_version INTEGER NOT NULL DEFAULT 0")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS content_file_sync (
            lesson_id TEXT PRIMARY KEY,
            last_error TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(lesson_id) REFERENCES lessons(lesson_id) ON DELETE CASCADE
        )
        """
    )


def _asset_verification_cache(db: sqlite3.Connection) -> None:
    _add_column(db, "lesson_assets", "file_mtime_ns INTEGER")
    _add_column(db, "lesson_assets", "last_verified_at TEXT")
    db.execute(
        "CREATE INDEX IF NOT EXISTS lesson_assets_verification ON lesson_assets(last_verified_at, deleted_at)"
    )


def _transcript_normalization(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS normalization_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lesson_id TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            configuration_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'pending', 'running', 'review_required', 'approved',
                'failed', 'stale', 'cancelled'
            )),
            attempts INTEGER NOT NULL DEFAULT 0,
            artifact_path TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            approved_at TEXT,
            FOREIGN KEY(lesson_id) REFERENCES lessons(lesson_id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS normalization_runs_logical_active
        ON normalization_runs(
            lesson_id, source_sha256, model, prompt_version, configuration_hash
        )
        WHERE status IN (
            'pending', 'running', 'review_required', 'failed', 'cancelled'
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS normalization_runs_lesson_created
        ON normalization_runs(lesson_id, created_at DESC)
        """
    )


def _resumable_normalization_chunks(db: sqlite3.Connection) -> None:
    _add_column(db, "normalization_runs", "provider TEXT NOT NULL DEFAULT 'ollama'")
    _add_column(db, "normalization_runs", "resume_count INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "normalization_runs", "last_resumed_at TEXT")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS normalization_chunks (
            run_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
            chunk_sha256 TEXT NOT NULL,
            target_ids_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'pending', 'running', 'completed', 'failed', 'indeterminate'
            )),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
            normalized_text TEXT,
            quality_json TEXT,
            response_sha256 TEXT,
            error TEXT,
            started_at TEXT,
            completed_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(run_id, chunk_index),
            FOREIGN KEY(run_id) REFERENCES normalization_runs(id) ON DELETE CASCADE,
            CHECK(
                status <> 'completed'
                OR (
                    normalized_text IS NOT NULL
                    AND quality_json IS NOT NULL
                    AND response_sha256 IS NOT NULL
                )
            )
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS normalization_chunks_run_status "
        "ON normalization_chunks(run_id, status, chunk_index)"
    )

def _cloud_processing_privacy(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS cloud_processing_consents (
            id TEXT PRIMARY KEY,
            lesson_id TEXT NOT NULL,
            run_id INTEGER,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            purpose TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            configuration_hash TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            subject_profile TEXT NOT NULL,
            segment_count INTEGER NOT NULL CHECK(segment_count >= 0),
            character_count INTEGER NOT NULL CHECK(character_count >= 0),
            chunk_count INTEGER NOT NULL CHECK(chunk_count >= 0),
            request_fingerprint TEXT NOT NULL,
            scope TEXT NOT NULL CHECK(scope IN ('once', 'session')),
            decision TEXT NOT NULL CHECK(decision IN ('allowed', 'denied')),
            created_at TEXT NOT NULL,
            expires_at TEXT,
            FOREIGN KEY(lesson_id) REFERENCES lessons(lesson_id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES normalization_runs(id) ON DELETE SET NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS cloud_request_events (
            id TEXT PRIMARY KEY,
            consent_id TEXT NOT NULL,
            run_id INTEGER,
            chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            event TEXT NOT NULL CHECK(event IN (
                'request_started', 'request_completed', 'request_failed',
                'request_indeterminate', 'retry_confirmed'
            )),
            request_fingerprint TEXT NOT NULL,
            response_sha256 TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY(consent_id) REFERENCES cloud_processing_consents(id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES normalization_runs(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS cloud_consents_lesson_created "
        "ON cloud_processing_consents(lesson_id, created_at DESC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS cloud_events_run_chunk "
        "ON cloud_request_events(run_id, chunk_index, created_at)"
    )


MIGRATIONS = (
    Migration(1, "student_content_domain", _content_domain),
    Migration(2, "student_content_indexes", _content_indexes),
    Migration(3, "student_content_editing", _content_editing),
    Migration(4, "student_content_trash", _content_trash),
    Migration(5, "student_content_hardening", _content_hardening),
    Migration(6, "content_write_consistency", _content_write_consistency),
    Migration(7, "asset_verification_cache", _asset_verification_cache),
    Migration(8, "transcript_normalization", _transcript_normalization),
    Migration(9, "resumable_normalization_chunks", _resumable_normalization_chunks),
    Migration(10, "cloud_processing_privacy", _cloud_processing_privacy),
)


def apply_migrations(db: sqlite3.Connection) -> None:
    """Create the core schema and apply every migration exactly once."""
    _create_core_schema(db)
    db.commit()
    applied = {int(row[0]) for row in db.execute("SELECT version FROM schema_migrations").fetchall()}
    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        try:
            db.execute("BEGIN IMMEDIATE")
            migration.apply(db)
            db.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
