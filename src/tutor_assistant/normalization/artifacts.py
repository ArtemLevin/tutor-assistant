from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..atomic_io import atomic_write_text
from .models import (
    NormalizationRun,
    NormalizationRunStatus,
    SourceSegment,
)

if TYPE_CHECKING:
    from ..content.repository import StudentContentRepository


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_sha256(segments: list[SourceSegment]) -> str:
    payload = [segment.model_dump(mode="json", exclude={"context_only"}) for segment in segments]
    return sha256_text(_canonical_json(payload))


def configuration_hash(payload: Any) -> str:
    return sha256_text(_canonical_json(payload))


def write_text_atomic(path: Path, text: str) -> Path:
    atomic_write_text(path, text.rstrip() + "\n")
    return path


def write_json_atomic(path: Path, payload: Any) -> Path:
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(mode="json")
    else:
        data = payload
    atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    )
    return path


class NormalizationRunStore:
    def __init__(self, repository: StudentContentRepository) -> None:
        self.repository = repository

    @staticmethod
    def _from_row(row: sqlite3.Row) -> NormalizationRun:
        return NormalizationRun.model_validate(dict(row))

    def recover_interrupted(self) -> int:
        with self.repository.connect() as db:
            cursor = db.execute(
                """
                UPDATE normalization_runs
                SET status='pending', error='interrupted_by_application_restart'
                WHERE status='running'
                """
            )
        return cursor.rowcount

    def create_or_get(
        self,
        *,
        lesson_id: str,
        source_hash: str,
        model: str,
        provider: str,
        prompt_version: str,
        config_hash: str,
        force: bool,
    ) -> tuple[NormalizationRun, bool]:
        now = datetime.now(UTC).isoformat()
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                UPDATE normalization_runs
                SET status='stale'
                WHERE lesson_id=? AND source_sha256<>? AND status IN (
                    'pending', 'running', 'review_required', 'failed', 'cancelled'
                )
                """,
                (lesson_id, source_hash),
            )
            logical = (lesson_id, source_hash, model, prompt_version, config_hash)
            if force:
                db.execute(
                    """
                    UPDATE normalization_runs SET status='stale'
                    WHERE lesson_id=? AND source_sha256=? AND model=?
                      AND prompt_version=? AND configuration_hash=?
                      AND status NOT IN ('stale', 'approved')
                    """,
                    logical,
                )
            else:
                row = db.execute(
                    """
                    SELECT * FROM normalization_runs
                    WHERE lesson_id=? AND source_sha256=? AND model=?
                      AND prompt_version=? AND configuration_hash=? AND status<>'stale'
                    ORDER BY id DESC LIMIT 1
                    """,
                    logical,
                ).fetchone()
                if row is not None:
                    return self._from_row(row), False
            cursor = db.execute(
                """
                INSERT INTO normalization_runs (
                    lesson_id, source_sha256, model, prompt_version,
                    configuration_hash, provider, status, attempts, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?)
                """,
                (*logical, provider, now),
            )
            row = db.execute(
                "SELECT * FROM normalization_runs WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone()
        return self._from_row(row), True

    def get(self, run_id: int) -> NormalizationRun | None:
        with self.repository.connect() as db:
            row = db.execute(
                "SELECT * FROM normalization_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def latest(self, lesson_id: str) -> NormalizationRun | None:
        with self.repository.connect() as db:
            row = db.execute(
                """
                SELECT * FROM normalization_runs
                WHERE lesson_id=? AND status<>'stale'
                ORDER BY id DESC LIMIT 1
                """,
                (lesson_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def list_for_lesson(self, lesson_id: str) -> list[NormalizationRun]:
        with self.repository.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM normalization_runs
                WHERE lesson_id=? ORDER BY id DESC
                """,
                (lesson_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def mark_running(self, run_id: int, *, resumed: bool = False) -> NormalizationRun:
        now = datetime.now(UTC).isoformat()
        with self.repository.connect() as db:
            db.execute(
                """
                UPDATE normalization_runs
                SET status='running', started_at=?, completed_at=NULL,
                    approved_at=NULL, artifact_path=NULL, error=NULL,
                    resume_count=resume_count + ?,
                    last_resumed_at=CASE WHEN ?=1 THEN ? ELSE last_resumed_at END
                WHERE id=?
                """,
                (now, int(resumed), int(resumed), now, run_id),
            )
        return self._required(run_id)

    def increment_attempts(self, run_id: int) -> None:
        with self.repository.connect() as db:
            db.execute(
                "UPDATE normalization_runs SET attempts=attempts+1 WHERE id=?",
                (run_id,),
            )

    def finish(
        self,
        run_id: int,
        status: NormalizationRunStatus,
        *,
        artifact_path: str | None = None,
        error: str | None = None,
    ) -> NormalizationRun:
        completed_at = (
            datetime.now(UTC).isoformat()
            if status
            in {
                NormalizationRunStatus.REVIEW_REQUIRED,
                NormalizationRunStatus.FAILED,
                NormalizationRunStatus.CANCELLED,
            }
            else None
        )
        approved_at = datetime.now(UTC).isoformat() if status == NormalizationRunStatus.APPROVED else None
        with self.repository.connect() as db:
            db.execute(
                """
                UPDATE normalization_runs
                SET status=?, artifact_path=COALESCE(?, artifact_path),
                    error=?, completed_at=COALESCE(?, completed_at),
                    approved_at=COALESCE(?, approved_at)
                WHERE id=?
                """,
                (
                    status.value,
                    artifact_path,
                    error[-2000:] if error else None,
                    completed_at,
                    approved_at,
                    run_id,
                ),
            )
        return self._required(run_id)

    def _required(self, run_id: int) -> NormalizationRun:
        run = self.get(run_id)
        if run is None:
            raise LookupError(f"Normalization run not found: {run_id}")
        return run
