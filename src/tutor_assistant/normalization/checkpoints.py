from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ..content.repository import StudentContentRepository
from .chunking import NormalizationChunk
from .errors import NormalizationCheckpointMismatchError
from .models import (
    NormalizationChunkCheckpoint,
    NormalizationChunkStatus,
    NormalizationQuality,
)

CHECKPOINT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class NormalizationChunkSpec:
    chunk_index: int
    chunk_sha256: str
    target_ids: tuple[int, ...]


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_sha256(
    chunk: NormalizationChunk,
    *,
    configuration_hash: str,
    prompt_version: str,
    subject_profile: str,
) -> str:
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "configuration_hash": configuration_hash,
        "prompt_version": prompt_version,
        "subject_profile": subject_profile,
        "chunk_index": chunk.index,
        "target_ids": list(chunk.target_ids),
        "segments": [item.model_dump(mode="json") for item in chunk.segments],
    }
    return sha256_text(_canonical_json(payload))


class NormalizationCheckpointStore:
    def __init__(self, repository: StudentContentRepository) -> None:
        self.repository = repository

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> NormalizationChunkCheckpoint:
        quality_json = row["quality_json"]
        return NormalizationChunkCheckpoint(
            run_id=int(row["run_id"]),
            chunk_index=int(row["chunk_index"]),
            chunk_sha256=str(row["chunk_sha256"]),
            target_ids=tuple(int(item) for item in json.loads(row["target_ids_json"])),
            status=NormalizationChunkStatus(str(row["status"])),
            attempts=int(row["attempts"]),
            normalized_text=row["normalized_text"],
            quality=(
                NormalizationQuality.model_validate_json(quality_json) if quality_json is not None else None
            ),
            response_sha256=row["response_sha256"],
            error=row["error"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            updated_at=row["updated_at"],
        )

    def prepare_chunks(
        self,
        run_id: int,
        chunks: list[NormalizationChunk],
        *,
        configuration_hash: str,
        prompt_version: str,
        subject_profile: str,
    ) -> list[NormalizationChunkCheckpoint]:
        specs = [
            NormalizationChunkSpec(
                chunk_index=chunk.index,
                chunk_sha256=chunk_sha256(
                    chunk,
                    configuration_hash=configuration_hash,
                    prompt_version=prompt_version,
                    subject_profile=subject_profile,
                ),
                target_ids=chunk.target_ids,
            )
            for chunk in chunks
        ]
        expected = {item.chunk_index: item for item in specs}
        now = self._now()
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM normalization_chunks WHERE run_id=? ORDER BY chunk_index",
                (run_id,),
            ).fetchall()
            for row in rows:
                index = int(row["chunk_index"])
                spec = expected.get(index)
                stored_targets = tuple(int(item) for item in json.loads(row["target_ids_json"]))
                if (
                    spec is None
                    or str(row["chunk_sha256"]) != spec.chunk_sha256
                    or stored_targets != spec.target_ids
                ):
                    raise NormalizationCheckpointMismatchError(
                        f"Checkpoint чанка {index} не соответствует текущему источнику или конфигурации"
                    )
            for spec in specs:
                db.execute(
                    """
                    INSERT OR IGNORE INTO normalization_chunks (
                        run_id, chunk_index, chunk_sha256, target_ids_json,
                        status, attempts, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', 0, ?)
                    """,
                    (
                        run_id,
                        spec.chunk_index,
                        spec.chunk_sha256,
                        _canonical_json(list(spec.target_ids)),
                        now,
                    ),
                )
        return self.list_for_run(run_id)

    def list_for_run(self, run_id: int) -> list[NormalizationChunkCheckpoint]:
        with self.repository.connect() as db:
            rows = db.execute(
                "SELECT * FROM normalization_chunks WHERE run_id=? ORDER BY chunk_index",
                (run_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def get(self, run_id: int, chunk_index: int) -> NormalizationChunkCheckpoint | None:
        with self.repository.connect() as db:
            row = db.execute(
                "SELECT * FROM normalization_chunks WHERE run_id=? AND chunk_index=?",
                (run_id, chunk_index),
            ).fetchone()
        return self._from_row(row) if row else None

    @staticmethod
    def _assert_transition(
        db: sqlite3.Connection,
        cursor: sqlite3.Cursor,
        run_id: int,
        chunk_index: int,
        target_status: NormalizationChunkStatus,
    ) -> None:
        if cursor.rowcount == 1:
            return
        row = db.execute(
            "SELECT status FROM normalization_chunks WHERE run_id=? AND chunk_index=?",
            (run_id, chunk_index),
        ).fetchone()
        if row is None:
            raise LookupError(f"Normalization chunk not found: {run_id}/{chunk_index}")
        current_status = str(row["status"])
        raise NormalizationCheckpointMismatchError(
            f"Недопустимый переход normalization checkpoint: {current_status} → {target_status.value}"
        )

    def mark_running(self, run_id: int, chunk_index: int) -> None:
        now = self._now()
        with self.repository.connect() as db:
            cursor = db.execute(
                """
                UPDATE normalization_chunks
                SET status='running', attempts=attempts+1, error=NULL,
                    started_at=?, completed_at=NULL, updated_at=?
                WHERE run_id=? AND chunk_index=? AND status IN ('pending', 'failed')
                """,
                (now, now, run_id, chunk_index),
            )
            self._assert_transition(db, cursor, run_id, chunk_index, NormalizationChunkStatus.RUNNING)

    def complete(
        self,
        run_id: int,
        chunk_index: int,
        *,
        normalized_text: str,
        quality: NormalizationQuality,
    ) -> None:
        now = self._now()
        digest = sha256_text(normalized_text)
        with self.repository.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """
                UPDATE normalization_chunks
                SET status='completed', normalized_text=?, quality_json=?,
                    response_sha256=?, error=NULL, completed_at=?, updated_at=?
                WHERE run_id=? AND chunk_index=? AND status='running'
                """,
                (
                    normalized_text,
                    quality.model_dump_json(),
                    digest,
                    now,
                    now,
                    run_id,
                    chunk_index,
                ),
            )
            self._assert_transition(db, cursor, run_id, chunk_index, NormalizationChunkStatus.COMPLETED)

    def fail(self, run_id: int, chunk_index: int, error: str) -> None:
        now = self._now()
        with self.repository.connect() as db:
            cursor = db.execute(
                """
                UPDATE normalization_chunks
                SET status='failed', error=?, updated_at=?
                WHERE run_id=? AND chunk_index=? AND status='running'
                """,
                (error[-2000:], now, run_id, chunk_index),
            )
            self._assert_transition(db, cursor, run_id, chunk_index, NormalizationChunkStatus.FAILED)

    def reset_pending(self, run_id: int, chunk_index: int) -> None:
        with self.repository.connect() as db:
            cursor = db.execute(
                """
                UPDATE normalization_chunks
                SET status='pending', error=NULL, updated_at=?
                WHERE run_id=? AND chunk_index=? AND status IN ('running', 'failed')
                """,
                (self._now(), run_id, chunk_index),
            )
            self._assert_transition(db, cursor, run_id, chunk_index, NormalizationChunkStatus.PENDING)

    def reset_indeterminate(self, run_id: int, chunk_index: int) -> None:
        with self.repository.connect() as db:
            cursor = db.execute(
                """
                UPDATE normalization_chunks
                SET status='pending', error='indeterminate_retry_confirmed', updated_at=?
                WHERE run_id=? AND chunk_index=? AND status='indeterminate'
                """,
                (self._now(), run_id, chunk_index),
            )
            self._assert_transition(db, cursor, run_id, chunk_index, NormalizationChunkStatus.PENDING)

    def mark_indeterminate(self, run_id: int, chunk_index: int, error: str) -> None:
        now = self._now()
        with self.repository.connect() as db:
            cursor = db.execute(
                """
                UPDATE normalization_chunks
                SET status='indeterminate', error=?, updated_at=?
                WHERE run_id=? AND chunk_index=? AND status='running'
                """,
                (error[-2000:], now, run_id, chunk_index),
            )
            self._assert_transition(
                db,
                cursor,
                run_id,
                chunk_index,
                NormalizationChunkStatus.INDETERMINATE,
            )

    def recover_interrupted(self) -> int:
        with self.repository.connect() as db:
            cursor = db.execute(
                """
                UPDATE normalization_chunks
                SET status='indeterminate', error='interrupted_during_provider_request', updated_at=?
                WHERE status='running'
                """,
                (self._now(),),
            )
        return cursor.rowcount

    def verify_completed(self, checkpoint: NormalizationChunkCheckpoint) -> None:
        if checkpoint.status != NormalizationChunkStatus.COMPLETED:
            return
        if checkpoint.normalized_text is None or checkpoint.quality is None:
            raise NormalizationCheckpointMismatchError(
                f"Checkpoint чанка {checkpoint.chunk_index} не содержит обязательных данных"
            )
        if checkpoint.response_sha256 != sha256_text(checkpoint.normalized_text):
            raise NormalizationCheckpointMismatchError(
                f"Контрольная сумма checkpoint чанка {checkpoint.chunk_index} не совпадает"
            )
