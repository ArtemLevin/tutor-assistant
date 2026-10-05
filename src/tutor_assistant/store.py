from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import TypeVar

from .content.migrations import apply_migrations
from .content.repository import StudentContentRepository
from .domain import Lesson
from .sqlite_utils import ClosingConnection
from .transcript_policy import transcript_has_content

T = TypeVar("T")


@dataclass(frozen=True)
class StoredTranscriptionJob:
    lesson_id: str
    audio_path: str
    status: str
    error: str | None
    attempts: int


@dataclass(frozen=True)
class StoredAutomaticPublicationJob:
    lesson_id: str
    revision_number: int
    content_sha256: str
    repository_path: str
    status: str
    attempts: int
    error: str | None
    next_attempt_at: str | None


class AutomaticPublicationJobConflictError(RuntimeError):
    pass


class LessonStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _retry(operation: Callable[[], T]) -> T:
        for attempt in range(5):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 4:
                    raise
                sleep(0.05 * (2**attempt))
        raise RuntimeError("unreachable")

    def _initialize(self) -> None:
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            apply_migrations(db)

    def save(self, lesson: Lesson) -> None:
        """Create a legacy lesson; updates must use StudentContentService."""

        repository = StudentContentRepository(self.path)
        if repository.get_lesson(lesson.lesson_id, include_deleted=True) is not None:
            raise RuntimeError(
                "LessonStore.save() больше не обновляет занятия; используйте StudentContentService"
            )
        repository.insert_lesson(lesson)

    def save_transcription_job(
        self,
        lesson_id: str,
        audio_path: str,
        status: str,
        error: str | None = None,
        *,
        increment_attempts: bool = False,
    ) -> None:
        def operation() -> None:
            with self.connect() as db:
                db.execute(
                    """
                    INSERT INTO transcription_jobs
                        (lesson_id, audio_path, status, error, attempts, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(lesson_id) DO UPDATE SET
                        audio_path=excluded.audio_path,
                        status=excluded.status,
                        error=excluded.error,
                        attempts=transcription_jobs.attempts + ?,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        lesson_id,
                        audio_path,
                        status,
                        error,
                        1 if increment_attempts else 0,
                        1 if increment_attempts else 0,
                    ),
                )

        self._retry(operation)

    def list_transcription_jobs(self) -> list[StoredTranscriptionJob]:
        def operation():
            with self.connect() as db:
                return db.execute(
                    """
                    SELECT lesson_id, audio_path, status, error, attempts
                    FROM transcription_jobs
                    ORDER BY updated_at ASC
                    """
                ).fetchall()

        rows = self._retry(operation)
        return [StoredTranscriptionJob(**dict(row)) for row in rows]

    def ensure_automatic_publication_job(
        self,
        lesson_id: str,
        revision_number: int,
        content_sha256: str,
        repository_path: str,
    ) -> StoredAutomaticPublicationJob:
        def operation() -> StoredAutomaticPublicationJob:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    """
                    SELECT lesson_id, revision_number, content_sha256, repository_path,
                           status, attempts, error, next_attempt_at
                    FROM automatic_publication_jobs
                    WHERE lesson_id=?
                    """,
                    (lesson_id,),
                ).fetchone()
                if row is None:
                    db.execute(
                        """
                        INSERT INTO automatic_publication_jobs (
                            lesson_id, revision_number, content_sha256, repository_path,
                            status, attempts, error, next_attempt_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'waiting', 0, NULL, NULL, CURRENT_TIMESTAMP)
                        """,
                        (lesson_id, revision_number, content_sha256, repository_path),
                    )
                    row = db.execute(
                        """
                        SELECT lesson_id, revision_number, content_sha256, repository_path,
                               status, attempts, error, next_attempt_at
                        FROM automatic_publication_jobs
                        WHERE lesson_id=?
                        """,
                        (lesson_id,),
                    ).fetchone()
                assert row is not None
                immutable = (
                    int(row["revision_number"]),
                    str(row["content_sha256"]),
                    str(row["repository_path"]),
                )
                expected = (revision_number, content_sha256, repository_path)
                if immutable != expected:
                    raise AutomaticPublicationJobConflictError(
                        "Automatic publication intent already exists "
                        "with different immutable payload"
                    )
                return StoredAutomaticPublicationJob(**dict(row))

        return self._retry(operation)

    @staticmethod
    def _validate_repair_revision(
        db: sqlite3.Connection,
        *,
        lesson_id: str,
        revision_number: int,
        content_sha256: str,
        expect_content: bool,
    ) -> None:
        row = db.execute(
            """
            SELECT content, content_sha256, created_by
            FROM transcript_revisions
            WHERE lesson_id=?
              AND revision_number=?
              AND content_sha256=?
              AND deleted_at IS NULL
            """,
            (lesson_id, revision_number, content_sha256),
        ).fetchone()
        if row is None:
            raise AutomaticPublicationJobConflictError(
                "Automatic transcript revision required for repair was not found"
            )
        content = str(row["content"])
        if str(row["created_by"]) != "automatic-transcription":
            raise AutomaticPublicationJobConflictError(
                "Publication intent repair requires automatic transcript revisions"
            )
        calculated_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if calculated_sha != str(row["content_sha256"]):
            raise AutomaticPublicationJobConflictError(
                "Automatic transcript revision SHA-256 mismatch"
            )
        if transcript_has_content(content) != expect_content:
            state = "meaningful" if expect_content else "empty"
            raise AutomaticPublicationJobConflictError(
                f"Publication intent repair requires a {state} automatic transcript revision"
            )

    def repair_automatic_publication_job(
        self,
        lesson_id: str,
        *,
        expected_revision_number: int,
        expected_content_sha256: str,
        expected_repository_path: str,
        revision_number: int,
        content_sha256: str,
        repository_path: str,
    ) -> StoredAutomaticPublicationJob:
        """Replace a legacy empty immutable payload before publication starts."""

        def operation() -> StoredAutomaticPublicationJob:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    """
                    SELECT lesson_id, revision_number, content_sha256, repository_path,
                           status, attempts, error, next_attempt_at
                    FROM automatic_publication_jobs
                    WHERE lesson_id=?
                    """,
                    (lesson_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(lesson_id)
                expected = (
                    expected_revision_number,
                    expected_content_sha256,
                    expected_repository_path,
                )
                actual = (
                    int(row["revision_number"]),
                    str(row["content_sha256"]),
                    str(row["repository_path"]),
                )
                if actual != expected:
                    raise AutomaticPublicationJobConflictError(
                        "Automatic publication intent changed before repair"
                    )
                if str(row["status"]) in {"running", "published"}:
                    raise AutomaticPublicationJobConflictError(
                        "Automatic publication intent cannot be repaired after publication starts"
                    )
                if revision_number <= expected_revision_number:
                    raise AutomaticPublicationJobConflictError(
                        "Automatic publication intent repair requires a newer revision"
                    )

                self._validate_repair_revision(
                    db,
                    lesson_id=lesson_id,
                    revision_number=expected_revision_number,
                    content_sha256=expected_content_sha256,
                    expect_content=False,
                )
                self._validate_repair_revision(
                    db,
                    lesson_id=lesson_id,
                    revision_number=revision_number,
                    content_sha256=content_sha256,
                    expect_content=True,
                )

                cursor = db.execute(
                    """
                    UPDATE automatic_publication_jobs
                    SET revision_number=?,
                        content_sha256=?,
                        repository_path=?,
                        status='waiting',
                        attempts=0,
                        error=NULL,
                        next_attempt_at=NULL,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE lesson_id=?
                      AND revision_number=?
                      AND content_sha256=?
                      AND repository_path=?
                      AND status NOT IN ('running', 'published')
                    """,
                    (
                        revision_number,
                        content_sha256,
                        repository_path,
                        lesson_id,
                        expected_revision_number,
                        expected_content_sha256,
                        expected_repository_path,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AutomaticPublicationJobConflictError(
                        "Automatic publication intent changed before repair"
                    )
                repaired = db.execute(
                    """
                    SELECT lesson_id, revision_number, content_sha256, repository_path,
                           status, attempts, error, next_attempt_at
                    FROM automatic_publication_jobs
                    WHERE lesson_id=?
                    """,
                    (lesson_id,),
                ).fetchone()
                assert repaired is not None
                return StoredAutomaticPublicationJob(**dict(repaired))

        return self._retry(operation)

    def quarantine_stale_empty_automatic_publication_job(
        self,
        lesson_id: str,
        *,
        expected_revision_number: int,
        expected_content_sha256: str,
        expected_repository_path: str,
        error: str,
    ) -> StoredAutomaticPublicationJob:
        """Quarantine a startup-recovered running job pinned to an empty revision."""

        def operation() -> StoredAutomaticPublicationJob:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._validate_repair_revision(
                    db,
                    lesson_id=lesson_id,
                    revision_number=expected_revision_number,
                    content_sha256=expected_content_sha256,
                    expect_content=False,
                )
                cursor = db.execute(
                    """
                    UPDATE automatic_publication_jobs
                    SET status='blocked',
                        error=?,
                        next_attempt_at=NULL,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE lesson_id=?
                      AND revision_number=?
                      AND content_sha256=?
                      AND repository_path=?
                      AND status='running'
                    """,
                    (
                        error,
                        lesson_id,
                        expected_revision_number,
                        expected_content_sha256,
                        expected_repository_path,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AutomaticPublicationJobConflictError(
                        "Stale running publication intent changed before quarantine"
                    )
                quarantined = db.execute(
                    """
                    SELECT lesson_id, revision_number, content_sha256, repository_path,
                           status, attempts, error, next_attempt_at
                    FROM automatic_publication_jobs
                    WHERE lesson_id=?
                    """,
                    (lesson_id,),
                ).fetchone()
                assert quarantined is not None
                return StoredAutomaticPublicationJob(**dict(quarantined))

        return self._retry(operation)

    def update_automatic_publication_job(
        self,
        lesson_id: str,
        status: str,
        *,
        error: str | None = None,
        next_attempt_at: str | None = None,
        increment_attempts: bool = False,
    ) -> StoredAutomaticPublicationJob:
        def operation() -> StoredAutomaticPublicationJob:
            with self.connect() as db:
                cursor = db.execute(
                    """
                    UPDATE automatic_publication_jobs
                    SET status=?,
                        error=?,
                        next_attempt_at=?,
                        attempts=attempts + ?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE lesson_id=?
                    """,
                    (
                        status,
                        error,
                        next_attempt_at,
                        1 if increment_attempts else 0,
                        lesson_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise KeyError(lesson_id)
                row = db.execute(
                    """
                    SELECT lesson_id, revision_number, content_sha256, repository_path,
                           status, attempts, error, next_attempt_at
                    FROM automatic_publication_jobs
                    WHERE lesson_id=?
                    """,
                    (lesson_id,),
                ).fetchone()
                assert row is not None
                return StoredAutomaticPublicationJob(**dict(row))

        return self._retry(operation)

    def get_automatic_publication_job(
        self,
        lesson_id: str,
    ) -> StoredAutomaticPublicationJob | None:
        def operation():
            with self.connect() as db:
                return db.execute(
                    """
                    SELECT lesson_id, revision_number, content_sha256, repository_path,
                           status, attempts, error, next_attempt_at
                    FROM automatic_publication_jobs
                    WHERE lesson_id=?
                    """,
                    (lesson_id,),
                ).fetchone()

        row = self._retry(operation)
        return StoredAutomaticPublicationJob(**dict(row)) if row is not None else None

    def list_automatic_publication_jobs(self) -> list[StoredAutomaticPublicationJob]:
        def operation():
            with self.connect() as db:
                return db.execute(
                    """
                    SELECT lesson_id, revision_number, content_sha256, repository_path,
                           status, attempts, error, next_attempt_at
                    FROM automatic_publication_jobs
                    ORDER BY updated_at ASC, lesson_id ASC
                    """
                ).fetchall()

        rows = self._retry(operation)
        return [StoredAutomaticPublicationJob(**dict(row)) for row in rows]

    def get(self, lesson_id: str) -> Lesson | None:
        def operation():
            with self.connect() as db:
                return db.execute("SELECT payload FROM lessons WHERE lesson_id=?", (lesson_id,)).fetchone()

        row = self._retry(operation)
        return Lesson.model_validate_json(row["payload"]) if row else None

    def list(self, limit: int = 100) -> list[Lesson]:
        def operation():
            with self.connect() as db:
                return db.execute(
                    "SELECT payload FROM lessons ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()

        rows = self._retry(operation)
        return [Lesson.model_validate_json(row["payload"]) for row in rows]
