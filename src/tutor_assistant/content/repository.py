from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from typing import TypeVar

from ..domain import Lesson
from .migrations import apply_migrations
from .models import (
    AssetKind,
    LessonAsset,
    LessonContent,
    LessonFilters,
    LessonPage,
    TranscriptRevision,
)

T = TypeVar("T")


class ContentNotFoundError(LookupError):
    pass


class ContentConflictError(ValueError):
    pass


class DuplicateAssetError(ContentConflictError):
    def __init__(self, sha256: str, lesson_id: str, relative_path: str) -> None:
        self.sha256 = sha256
        self.lesson_id = lesson_id
        self.relative_path = relative_path
        super().__init__(f"Файл уже зарегистрирован: {lesson_id}/{relative_path}")


class StudentContentRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA synchronous=NORMAL")
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
            apply_migrations(db)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _lesson_from_row(row: sqlite3.Row) -> Lesson:
        return Lesson.model_validate_json(row["payload"])

    @staticmethod
    def _asset_from_row(row: sqlite3.Row) -> LessonAsset:
        return LessonAsset.model_validate(dict(row))

    @staticmethod
    def _revision_from_row(row: sqlite3.Row) -> TranscriptRevision:
        return TranscriptRevision.model_validate(dict(row))

    def upsert_lesson(self, lesson: Lesson) -> None:
        payload = lesson.model_dump_json()

        def operation() -> None:
            with self.connect() as db:
                db.execute(
                    """
                    INSERT INTO lessons (
                        lesson_id, student_id, lesson_date, topic, status, payload,
                        updated_at, subject, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(lesson_id) DO UPDATE SET
                        student_id=excluded.student_id,
                        lesson_date=excluded.lesson_date,
                        topic=excluded.topic,
                        status=excluded.status,
                        payload=excluded.payload,
                        updated_at=excluded.updated_at,
                        subject=excluded.subject,
                        created_at=excluded.created_at
                    """,
                    (
                        lesson.lesson_id,
                        lesson.student.id,
                        lesson.lesson_date.isoformat(),
                        lesson.topic,
                        lesson.status.value,
                        payload,
                        lesson.updated_at.isoformat(),
                        lesson.subject,
                        lesson.created_at.isoformat(),
                    ),
                )

        self._retry(operation)

    def get_lesson(self, lesson_id: str, *, include_deleted: bool = False) -> Lesson | None:
        def operation() -> sqlite3.Row | None:
            with self.connect() as db:
                sql = "SELECT payload FROM lessons WHERE lesson_id=?"
                if not include_deleted:
                    sql += " AND deleted_at IS NULL"
                return db.execute(sql, (lesson_id,)).fetchone()

        row = self._retry(operation)
        return self._lesson_from_row(row) if row else None

    def get_content(self, lesson_id: str, *, include_deleted: bool = False) -> LessonContent:
        def operation() -> sqlite3.Row | None:
            with self.connect() as db:
                sql = "SELECT payload, deleted_at FROM lessons WHERE lesson_id=?"
                if not include_deleted:
                    sql += " AND deleted_at IS NULL"
                return db.execute(sql, (lesson_id,)).fetchone()

        row = self._retry(operation)
        if row is None:
            raise ContentNotFoundError(f"Занятие не найдено: {lesson_id}")
        return LessonContent(
            lesson=self._lesson_from_row(row),
            assets=self.list_assets(lesson_id, include_deleted=include_deleted),
            transcript=self.current_transcript(lesson_id, include_deleted=include_deleted),
            deleted_at=row["deleted_at"],
        )

    def list_lessons(self, filters: LessonFilters | None = None) -> LessonPage:
        filters = filters or LessonFilters()
        clauses: list[str] = []
        parameters: list[str] = []
        if not filters.include_deleted:
            clauses.append("deleted_at IS NULL")
        if filters.student_id:
            clauses.append("student_id = ?")
            parameters.append(filters.student_id)
        if filters.subject:
            clauses.append("subject = ?")
            parameters.append(filters.subject)
        if filters.status:
            clauses.append("status = ?")
            parameters.append(filters.status.value)
        if filters.lesson_date_from:
            clauses.append("lesson_date >= ?")
            parameters.append(filters.lesson_date_from.isoformat())
        if filters.lesson_date_to:
            clauses.append("lesson_date <= ?")
            parameters.append(filters.lesson_date_to.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        def operation() -> tuple[int, list[sqlite3.Row]]:
            with self.connect() as db:
                if filters.query:
                    rows = db.execute(
                        f"""
                        SELECT payload, topic, student_id, subject
                        FROM lessons
                        {where}
                        ORDER BY lesson_date DESC, updated_at DESC, lesson_id ASC
                        """,
                        parameters,
                    ).fetchall()
                    needle = filters.query.casefold()
                    matched = [
                        row
                        for row in rows
                        if needle
                        in "\n".join(
                            (
                                str(row["topic"]),
                                str(row["student_id"]),
                                str(row["subject"]),
                                str(row["payload"]),
                            )
                        ).casefold()
                    ]
                    return len(matched), matched[filters.offset : filters.offset + filters.limit]
                total = int(db.execute(f"SELECT COUNT(*) FROM lessons{where}", parameters).fetchone()[0])
                rows = db.execute(
                    f"""
                    SELECT payload
                    FROM lessons
                    {where}
                    ORDER BY lesson_date DESC, updated_at DESC, lesson_id ASC
                    LIMIT ? OFFSET ?
                    """,
                    [*parameters, filters.limit, filters.offset],
                ).fetchall()
                return total, rows

        total, rows = self._retry(operation)
        return LessonPage(
            items=[self._lesson_from_row(row) for row in rows],
            total=total,
            limit=filters.limit,
            offset=filters.offset,
        )

    def set_lesson_deleted(self, lesson_id: str, *, deleted: bool) -> None:
        timestamp = self._now() if deleted else None

        def operation() -> None:
            with self.connect() as db:
                cursor = db.execute(
                    "UPDATE lessons SET deleted_at=? WHERE lesson_id=?",
                    (timestamp, lesson_id),
                )
                if cursor.rowcount == 0:
                    raise ContentNotFoundError(f"Занятие не найдено: {lesson_id}")

        self._retry(operation)

    def find_asset_by_sha256(
        self,
        sha256: str,
        *,
        kind: AssetKind | None = None,
    ) -> LessonAsset | None:
        def operation() -> sqlite3.Row | None:
            with self.connect() as db:
                sql = (
                    "SELECT a.id, a.lesson_id, a.kind, a.relative_path, a.media_type, "
                    "a.size_bytes, a.sha256, a.created_at, a.updated_at, a.deleted_at "
                    "FROM lesson_assets a "
                    "JOIN lessons l ON l.lesson_id=a.lesson_id "
                    "WHERE a.sha256=? AND a.deleted_at IS NULL AND l.deleted_at IS NULL"
                )
                parameters: list[str] = [sha256]
                if kind:
                    sql += " AND a.kind=?"
                    parameters.append(kind.value)
                sql += " ORDER BY a.id LIMIT 1"
                return db.execute(sql, parameters).fetchone()

        row = self._retry(operation)
        return self._asset_from_row(row) if row else None

    def import_lesson_bundle(
        self,
        lesson: Lesson,
        assets: list[LessonAsset],
        transcript: TranscriptRevision | None = None,
    ) -> None:
        """Insert a staged lesson and every imported record in one transaction."""

        def operation() -> None:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute(
                    "SELECT 1 FROM lessons WHERE lesson_id=?",
                    (lesson.lesson_id,),
                ).fetchone():
                    raise ContentConflictError(f"Занятие уже существует: {lesson.lesson_id}")
                for asset in assets:
                    if asset.kind != AssetKind.AUDIO:
                        continue
                    duplicate = db.execute(
                        """
                        SELECT a.lesson_id, a.relative_path
                        FROM lesson_assets a
                        JOIN lessons l ON l.lesson_id=a.lesson_id
                        WHERE a.sha256=? AND a.kind=?
                          AND a.deleted_at IS NULL AND l.deleted_at IS NULL
                        ORDER BY a.id LIMIT 1
                        """,
                        (asset.sha256, AssetKind.AUDIO.value),
                    ).fetchone()
                    if duplicate:
                        raise DuplicateAssetError(
                            asset.sha256,
                            str(duplicate["lesson_id"]),
                            str(duplicate["relative_path"]),
                        )

                db.execute(
                    """
                    INSERT INTO lessons (
                        lesson_id, student_id, lesson_date, topic, status, payload,
                        updated_at, subject, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lesson.lesson_id,
                        lesson.student.id,
                        lesson.lesson_date.isoformat(),
                        lesson.topic,
                        lesson.status.value,
                        lesson.model_dump_json(),
                        lesson.updated_at.isoformat(),
                        lesson.subject,
                        lesson.created_at.isoformat(),
                    ),
                )
                for asset in assets:
                    db.execute(
                        """
                        INSERT INTO lesson_assets (
                            lesson_id, kind, relative_path, media_type, size_bytes,
                            sha256, created_at, updated_at, deleted_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            asset.lesson_id,
                            asset.kind.value,
                            asset.relative_path,
                            asset.media_type,
                            asset.size_bytes,
                            asset.sha256,
                            asset.created_at.isoformat(),
                            asset.updated_at.isoformat(),
                            asset.deleted_at.isoformat() if asset.deleted_at else None,
                        ),
                    )
                if transcript:
                    db.execute(
                        """
                        INSERT INTO transcript_revisions (
                            lesson_id, revision_number, relative_path, content,
                            content_sha256, created_by, created_at, deleted_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            transcript.lesson_id,
                            1,
                            transcript.relative_path,
                            transcript.content,
                            transcript.content_sha256,
                            transcript.created_by,
                            transcript.created_at.isoformat(),
                            transcript.deleted_at.isoformat() if transcript.deleted_at else None,
                        ),
                    )

        self._retry(operation)

    def upsert_asset(self, asset: LessonAsset) -> LessonAsset:
        now = self._now()

        def operation() -> sqlite3.Row:
            with self.connect() as db:
                db.execute(
                    """
                    INSERT INTO lesson_assets (
                        lesson_id, kind, relative_path, media_type, size_bytes, sha256,
                        created_at, updated_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(lesson_id, relative_path) DO UPDATE SET
                        kind=excluded.kind,
                        media_type=excluded.media_type,
                        size_bytes=excluded.size_bytes,
                        sha256=excluded.sha256,
                        updated_at=excluded.updated_at
                    """,
                    (
                        asset.lesson_id,
                        asset.kind.value,
                        asset.relative_path,
                        asset.media_type,
                        asset.size_bytes,
                        asset.sha256,
                        asset.created_at.isoformat(),
                        now,
                        asset.deleted_at.isoformat() if asset.deleted_at else None,
                    ),
                )
                return db.execute(
                    """
                    SELECT id, lesson_id, kind, relative_path, media_type, size_bytes,
                           sha256, created_at, updated_at, deleted_at
                    FROM lesson_assets
                    WHERE lesson_id=? AND relative_path=?
                    """,
                    (asset.lesson_id, asset.relative_path),
                ).fetchone()

        return self._asset_from_row(self._retry(operation))

    def list_assets(self, lesson_id: str, *, include_deleted: bool = False) -> list[LessonAsset]:
        def operation() -> list[sqlite3.Row]:
            with self.connect() as db:
                sql = (
                    "SELECT id, lesson_id, kind, relative_path, media_type, size_bytes, "
                    "sha256, created_at, updated_at, deleted_at "
                    "FROM lesson_assets WHERE lesson_id=?"
                )
                if not include_deleted:
                    sql += " AND deleted_at IS NULL"
                sql += " ORDER BY kind, relative_path"
                return db.execute(sql, (lesson_id,)).fetchall()

        return [self._asset_from_row(row) for row in self._retry(operation)]

    def set_asset_deleted(self, asset_id: int, *, deleted: bool) -> None:
        timestamp = self._now() if deleted else None

        def operation() -> None:
            with self.connect() as db:
                cursor = db.execute(
                    "UPDATE lesson_assets SET deleted_at=?, updated_at=? WHERE id=?",
                    (timestamp, self._now(), asset_id),
                )
                if cursor.rowcount == 0:
                    raise ContentNotFoundError(f"Файл занятия не найден: {asset_id}")

        self._retry(operation)

    def add_transcript_revision(
        self, revision: TranscriptRevision, *, deduplicate: bool = False
    ) -> TranscriptRevision:
        def operation() -> sqlite3.Row:
            with self.connect() as db:
                if deduplicate:
                    existing = db.execute(
                        """
                        SELECT id, lesson_id, revision_number, relative_path, content,
                               content_sha256, created_by, created_at, deleted_at
                        FROM transcript_revisions
                        WHERE lesson_id=? AND relative_path=? AND content_sha256=?
                        ORDER BY revision_number DESC LIMIT 1
                        """,
                        (revision.lesson_id, revision.relative_path, revision.content_sha256),
                    ).fetchone()
                    if existing:
                        return existing
                number = int(
                    db.execute(
                        """
                        SELECT COALESCE(MAX(revision_number), 0) + 1
                        FROM transcript_revisions WHERE lesson_id=?
                        """,
                        (revision.lesson_id,),
                    ).fetchone()[0]
                )
                cursor = db.execute(
                    """
                    INSERT INTO transcript_revisions (
                        lesson_id, revision_number, relative_path, content,
                        content_sha256, created_by, created_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision.lesson_id,
                        number,
                        revision.relative_path,
                        revision.content,
                        revision.content_sha256,
                        revision.created_by,
                        revision.created_at.isoformat(),
                        revision.deleted_at.isoformat() if revision.deleted_at else None,
                    ),
                )
                return db.execute(
                    """
                    SELECT id, lesson_id, revision_number, relative_path, content,
                           content_sha256, created_by, created_at, deleted_at
                    FROM transcript_revisions WHERE id=?
                    """,
                    (cursor.lastrowid,),
                ).fetchone()

        return self._revision_from_row(self._retry(operation))

    def list_transcript_revisions(
        self, lesson_id: str, *, include_deleted: bool = False
    ) -> list[TranscriptRevision]:
        def operation() -> list[sqlite3.Row]:
            with self.connect() as db:
                sql = (
                    "SELECT id, lesson_id, revision_number, relative_path, content, "
                    "content_sha256, created_by, created_at, deleted_at "
                    "FROM transcript_revisions WHERE lesson_id=?"
                )
                if not include_deleted:
                    sql += " AND deleted_at IS NULL"
                sql += " ORDER BY revision_number DESC"
                return db.execute(sql, (lesson_id,)).fetchall()

        return [self._revision_from_row(row) for row in self._retry(operation)]

    def get_transcript_revision(
        self, revision_id: int, *, include_deleted: bool = False
    ) -> TranscriptRevision | None:
        def operation() -> sqlite3.Row | None:
            with self.connect() as db:
                sql = (
                    "SELECT id, lesson_id, revision_number, relative_path, content, "
                    "content_sha256, created_by, created_at, deleted_at "
                    "FROM transcript_revisions WHERE id=?"
                )
                if not include_deleted:
                    sql += " AND deleted_at IS NULL"
                return db.execute(sql, (revision_id,)).fetchone()

        row = self._retry(operation)
        return self._revision_from_row(row) if row else None

    def current_transcript(
        self, lesson_id: str, *, include_deleted: bool = False
    ) -> TranscriptRevision | None:
        revisions = self.list_transcript_revisions(lesson_id, include_deleted=include_deleted)
        return revisions[0] if revisions else None

    def set_transcript_deleted(self, revision_id: int, *, deleted: bool) -> None:
        timestamp = self._now() if deleted else None

        def operation() -> None:
            with self.connect() as db:
                cursor = db.execute(
                    "UPDATE transcript_revisions SET deleted_at=? WHERE id=?",
                    (timestamp, revision_id),
                )
                if cursor.rowcount == 0:
                    raise ContentNotFoundError(f"Версия транскрипта не найдена: {revision_id}")

        self._retry(operation)

    def applied_migrations(self) -> list[tuple[int, str]]:
        with self.connect() as db:
            rows = db.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
        return [(int(row["version"]), str(row["name"])) for row in rows]
