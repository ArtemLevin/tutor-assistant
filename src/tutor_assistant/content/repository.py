from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Collection
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import sleep
from typing import TypeVar

from ..domain import Lesson, Student
from ..sqlite_utils import ClosingConnection
from .migrations import apply_migrations
from .models import (
    AssetKind,
    ContentOperation,
    ContentOperationKind,
    ContentOperationStatus,
    LessonAsset,
    LessonContent,
    LessonFilters,
    LessonPage,
    TranscriptDraft,
    TranscriptRevision,
    TrashEntry,
    TrashItem,
    TrashState,
)

T = TypeVar("T")


class ContentNotFoundError(LookupError):
    pass


class ContentConflictError(ValueError):
    pass


class LessonEditConflictError(ContentConflictError):
    pass


class TranscriptEditConflictError(ContentConflictError):
    def __init__(self, expected: int | None, current: int | None) -> None:
        self.expected = expected
        self.current = current
        super().__init__(
            "Транскрипт уже изменён в другом окне: "
            f"ожидалась версия {expected or 'без версии'}, текущая — {current or 'без версии'}"
        )


class ActiveLessonError(ContentConflictError):
    pass


class DuplicateAssetError(ContentConflictError):
    def __init__(self, sha256: str, lesson_id: str, relative_path: str) -> None:
        self.sha256 = sha256
        self.lesson_id = lesson_id
        self.relative_path = relative_path
        super().__init__(f"Файл уже зарегистрирован: {lesson_id}/{relative_path}")


PIPELINE_WRITABLE_FIELDS = frozenset(
    {
        "status",
        "error",
        "source_audio_local",
        "artifacts",
        "publication",
        "latex",
    }
)


class StudentContentRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
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

    @staticmethod
    def _draft_from_row(row: sqlite3.Row) -> TranscriptDraft:
        return TranscriptDraft.model_validate(dict(row))

    @staticmethod
    def _trash_from_row(row: sqlite3.Row) -> TrashEntry:
        return TrashEntry.model_validate(
            {
                key: row[key]
                for key in (
                    "lesson_id",
                    "original_relative_path",
                    "trash_relative_path",
                    "staging_relative_path",
                    "size_bytes",
                    "state",
                    "deleted_at",
                    "purge_after",
                )
            }
        )

    @staticmethod
    def _operation_from_row(row: sqlite3.Row) -> ContentOperation:
        return ContentOperation.model_validate(dict(row))

    @staticmethod
    def _fts_available(db: sqlite3.Connection) -> bool:
        try:
            row = db.execute("SELECT enabled FROM content_capabilities WHERE name='fts5'").fetchone()
        except sqlite3.OperationalError:
            return False
        return bool(row and row[0])

    @staticmethod
    def _fts_query(value: str) -> str:
        tokens = re.findall(r"\w+", value.casefold(), flags=re.UNICODE)
        return " AND ".join(f'"{token}"*' for token in tokens)

    @classmethod
    def _refresh_search_document(cls, db: sqlite3.Connection, lesson_id: str) -> None:
        if not cls._fts_available(db):
            return
        db.execute("DELETE FROM lesson_search WHERE lesson_id=?", (lesson_id,))
        row = db.execute(
            """
            SELECT l.payload,
                   COALESCE((
                       SELECT r.content FROM transcript_revisions r
                       WHERE r.lesson_id=l.lesson_id AND r.deleted_at IS NULL
                       ORDER BY r.revision_number DESC LIMIT 1
                   ), '') AS transcript
            FROM lessons l WHERE l.lesson_id=?
            """,
            (lesson_id,),
        ).fetchone()
        if row:
            db.execute(
                "INSERT INTO lesson_search (lesson_id, metadata, transcript) VALUES (?, ?, ?)",
                (lesson_id, str(row["payload"]), str(row["transcript"])),
            )

    @classmethod
    def _mark_file_sync(cls, db: sqlite3.Connection, lesson_id: str) -> None:
        db.execute(
            """
            INSERT INTO content_file_sync (lesson_id, last_error, updated_at)
            VALUES (?, NULL, ?)
            ON CONFLICT(lesson_id) DO UPDATE SET
                last_error=NULL,
                updated_at=excluded.updated_at
            """,
            (lesson_id, cls._now()),
        )

    def insert_lesson(self, lesson: Lesson) -> None:
        def operation() -> None:
            with self.connect() as db:
                try:
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
                except sqlite3.IntegrityError as exc:
                    raise ContentConflictError(f"Занятие уже существует: {lesson.lesson_id}") from exc
                self._mark_file_sync(db, lesson.lesson_id)
                self._refresh_search_document(db, lesson.lesson_id)

        self._retry(operation)

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
                        created_at=excluded.created_at,
                        row_version=lessons.row_version + 1
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
                self._mark_file_sync(db, lesson.lesson_id)
                self._refresh_search_document(db, lesson.lesson_id)

        self._retry(operation)

    def replace_lesson(self, incoming: Lesson, *, expected_row_version: int) -> Lesson:
        def operation() -> Lesson:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    """
                    SELECT row_version FROM lessons
                    WHERE lesson_id=? AND deleted_at IS NULL
                    """,
                    (incoming.lesson_id,),
                ).fetchone()
                if row is None:
                    raise ContentNotFoundError(f"Занятие не найдено: {incoming.lesson_id}")
                if int(row["row_version"]) != expected_row_version:
                    raise LessonEditConflictError(
                        "Занятие уже изменено другим процессом. Обновите данные и повторите."
                    )
                replacement = incoming.model_copy(deep=True)
                replacement.updated_at = datetime.now(UTC)
                cursor = db.execute(
                    """
                    UPDATE lessons SET student_id=?, lesson_date=?, topic=?, status=?, payload=?,
                        updated_at=?, subject=?, created_at=?, row_version=row_version + 1
                    WHERE lesson_id=? AND row_version=? AND deleted_at IS NULL
                    """,
                    (
                        replacement.student.id,
                        replacement.lesson_date.isoformat(),
                        replacement.topic,
                        replacement.status.value,
                        replacement.model_dump_json(),
                        replacement.updated_at.isoformat(),
                        replacement.subject,
                        replacement.created_at.isoformat(),
                        replacement.lesson_id,
                        expected_row_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LessonEditConflictError(
                        "Занятие уже изменено другим процессом. Обновите данные и повторите."
                    )
                self._mark_file_sync(db, replacement.lesson_id)
                self._refresh_search_document(db, replacement.lesson_id)
                return replacement

        return self._retry(operation)

    def update_pipeline_lesson(
        self,
        incoming: Lesson,
        fields: Collection[str],
        *,
        expected_row_version: int | None = None,
        force_status: bool = False,
    ) -> Lesson:
        selected = frozenset(fields)
        unknown = selected - PIPELINE_WRITABLE_FIELDS
        if unknown:
            raise ValueError(f"Pipeline не может изменять поля: {', '.join(sorted(unknown))}")
        if not selected:
            raise ValueError("Не указаны поля pipeline для сохранения")

        def operation() -> Lesson:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    """
                    SELECT payload, row_version FROM lessons
                    WHERE lesson_id=? AND deleted_at IS NULL
                    """,
                    (incoming.lesson_id,),
                ).fetchone()
                if row is None:
                    raise ContentNotFoundError(f"Занятие не найдено: {incoming.lesson_id}")
                row_version = int(row["row_version"])
                if expected_row_version is not None and row_version != expected_row_version:
                    raise LessonEditConflictError(
                        "Занятие уже изменено другим процессом. Обновите данные и повторите."
                    )
                current = self._lesson_from_row(row)
                if "status" in selected:
                    error = incoming.error if "error" in selected else current.error
                    current.transition(incoming.status, error, force=force_status)
                elif "error" in selected:
                    current.error = incoming.error
                for field in selected - {"status", "error"}:
                    setattr(current, field, deepcopy(getattr(incoming, field)))
                current.updated_at = datetime.now(UTC)
                cursor = db.execute(
                    """
                    UPDATE lessons SET status=?, payload=?, updated_at=?, row_version=row_version + 1
                    WHERE lesson_id=? AND row_version=? AND deleted_at IS NULL
                    """,
                    (
                        current.status.value,
                        current.model_dump_json(),
                        current.updated_at.isoformat(),
                        current.lesson_id,
                        row_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LessonEditConflictError(
                        "Занятие уже изменено другим процессом. Обновите данные и повторите."
                    )
                self._mark_file_sync(db, current.lesson_id)
                self._refresh_search_document(db, current.lesson_id)
                return current

        return self._retry(operation)

    def complete_file_sync(self, lesson_id: str) -> None:
        def operation() -> None:
            with self.connect() as db:
                db.execute("DELETE FROM content_file_sync WHERE lesson_id=?", (lesson_id,))

        self._retry(operation)

    def fail_file_sync(self, lesson_id: str, details: str) -> None:
        def operation() -> None:
            with self.connect() as db:
                db.execute(
                    """
                    UPDATE content_file_sync SET last_error=?, updated_at=? WHERE lesson_id=?
                    """,
                    (details[-3000:], self._now(), lesson_id),
                )

        self._retry(operation)

    def pending_file_sync(self) -> list[tuple[str, str | None]]:
        def operation() -> list[sqlite3.Row]:
            with self.connect() as db:
                return db.execute(
                    """
                    SELECT lesson_id, last_error FROM content_file_sync ORDER BY updated_at
                    """
                ).fetchall()

        return [
            (str(row["lesson_id"]), str(row["last_error"]) if row["last_error"] else None)
            for row in self._retry(operation)
        ]

    def get_lesson(self, lesson_id: str, *, include_deleted: bool = False) -> Lesson | None:
        def operation() -> sqlite3.Row | None:
            with self.connect() as db:
                sql = "SELECT payload FROM lessons WHERE lesson_id=?"
                if not include_deleted:
                    sql += " AND deleted_at IS NULL"
                return db.execute(sql, (lesson_id,)).fetchone()

        row = self._retry(operation)
        return self._lesson_from_row(row) if row else None

    def lesson_row_version(self, lesson_id: str) -> int:
        def operation() -> sqlite3.Row | None:
            with self.connect() as db:
                return db.execute(
                    "SELECT row_version FROM lessons WHERE lesson_id=?",
                    (lesson_id,),
                ).fetchone()

        row = self._retry(operation)
        if row is None:
            raise ContentNotFoundError(f"Занятие не найдено: {lesson_id}")
        return int(row["row_version"])

    def get_content(self, lesson_id: str, *, include_deleted: bool = False) -> LessonContent:
        def operation() -> sqlite3.Row | None:
            with self.connect() as db:
                sql = "SELECT payload, deleted_at, row_version FROM lessons WHERE lesson_id=?"
                if not include_deleted:
                    sql += " AND deleted_at IS NULL"
                return db.execute(sql, (lesson_id,)).fetchone()

        row = self._retry(operation)
        if row is None:
            raise ContentNotFoundError(f"Занятие не найдено: {lesson_id}")
        return LessonContent(
            lesson=self._lesson_from_row(row),
            row_version=int(row["row_version"]),
            assets=self.list_assets(lesson_id, include_deleted=include_deleted),
            transcript=self.current_transcript(lesson_id, include_deleted=include_deleted),
            draft=self.get_transcript_draft(lesson_id),
            deleted_at=row["deleted_at"],
        )

    def update_lesson_metadata(
        self,
        lesson_id: str,
        *,
        student: Student,
        subject: str,
        lesson_date: date,
        topic: str,
        expected_updated_at: datetime,
        expected_row_version: int | None = None,
    ) -> Lesson:
        def operation() -> Lesson:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    """
                    SELECT payload, updated_at, row_version FROM lessons
                    WHERE lesson_id=? AND deleted_at IS NULL
                    """,
                    (lesson_id,),
                ).fetchone()
                if row is None:
                    raise ContentNotFoundError(f"Занятие не найдено: {lesson_id}")
                current = self._lesson_from_row(row)
                if expected_row_version is not None:
                    conflict = int(row["row_version"]) != expected_row_version
                else:
                    conflict = current.updated_at != expected_updated_at
                if conflict:
                    raise LessonEditConflictError(
                        "Карточка занятия уже изменена в другом окне. Обновите данные и повторите."
                    )
                current.student = student
                current.subject = subject
                current.lesson_date = lesson_date
                current.topic = topic
                current.mark_generated_materials_stale()
                current.updated_at = datetime.now(UTC)
                db.execute(
                    """
                    UPDATE lessons SET student_id=?, lesson_date=?, topic=?, status=?, payload=?,
                        updated_at=?, subject=?, created_at=?, row_version=row_version + 1
                    WHERE lesson_id=?
                    """,
                    (
                        current.student.id,
                        current.lesson_date.isoformat(),
                        current.topic,
                        current.status.value,
                        current.model_dump_json(),
                        current.updated_at.isoformat(),
                        current.subject,
                        current.created_at.isoformat(),
                        current.lesson_id,
                    ),
                )
                self._mark_file_sync(db, current.lesson_id)
                self._refresh_search_document(db, current.lesson_id)
                return current

        return self._retry(operation)

    def list_lessons(self, filters: LessonFilters | None = None) -> LessonPage:
        filters = filters or LessonFilters()
        clauses: list[str] = []
        parameters: list[str] = []
        if not filters.include_deleted:
            clauses.append("l.deleted_at IS NULL")
        if filters.student_id:
            clauses.append("l.student_id = ?")
            parameters.append(filters.student_id)
        if filters.subject:
            clauses.append("l.subject = ?")
            parameters.append(filters.subject)
        if filters.status:
            clauses.append("l.status = ?")
            parameters.append(filters.status.value)
        if filters.lesson_date_from:
            clauses.append("l.lesson_date >= ?")
            parameters.append(filters.lesson_date_from.isoformat())
        if filters.lesson_date_to:
            clauses.append("l.lesson_date <= ?")
            parameters.append(filters.lesson_date_to.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        def operation() -> tuple[int, list[sqlite3.Row]]:
            with self.connect() as db:
                if filters.query:
                    match = self._fts_query(filters.query)
                    if match and self._fts_available(db):
                        search_where = " WHERE lesson_search MATCH ?"
                        if clauses:
                            search_where += f" AND {' AND '.join(clauses)}"
                        search_parameters = [match, *parameters]
                        total = int(
                            db.execute(
                                f"""
                                SELECT COUNT(*) FROM lesson_search
                                JOIN lessons l ON l.lesson_id=lesson_search.lesson_id
                                {search_where}
                                """,
                                search_parameters,
                            ).fetchone()[0]
                        )
                        rows = db.execute(
                            f"""
                            SELECT l.payload FROM lesson_search
                            JOIN lessons l ON l.lesson_id=lesson_search.lesson_id
                            {search_where}
                            ORDER BY bm25(lesson_search), l.lesson_date DESC,
                                     l.updated_at DESC, l.lesson_id ASC
                            LIMIT ? OFFSET ?
                            """,
                            [*search_parameters, filters.limit, filters.offset],
                        ).fetchall()
                        return total, rows
                    rows = db.execute(
                        f"""
                        SELECT l.payload, l.topic, l.student_id, l.subject,
                               COALESCE((
                                   SELECT r.content FROM transcript_revisions r
                                   WHERE r.lesson_id=l.lesson_id AND r.deleted_at IS NULL
                                   ORDER BY r.revision_number DESC LIMIT 1
                               ), '') AS transcript
                        FROM lessons l
                        {where}
                        ORDER BY l.lesson_date DESC, l.updated_at DESC, l.lesson_id ASC
                        """,
                        parameters,
                    ).fetchall()
                    needles = re.findall(r"\w+", filters.query.casefold(), flags=re.UNICODE)
                    if not needles:
                        needles = [filters.query.casefold()]
                    matched = [
                        row
                        for row in rows
                        if all(
                            needle
                            in "\n".join(
                                (
                                    str(row["topic"]),
                                    str(row["student_id"]),
                                    str(row["subject"]),
                                    str(row["payload"]),
                                    str(row["transcript"]),
                                )
                            ).casefold()
                            for needle in needles
                        )
                    ]
                    return len(matched), matched[filters.offset : filters.offset + filters.limit]
                total = int(db.execute(f"SELECT COUNT(*) FROM lessons l{where}", parameters).fetchone()[0])
                rows = db.execute(
                    f"""
                    SELECT l.payload
                    FROM lessons l
                    {where}
                    ORDER BY l.lesson_date DESC, l.updated_at DESC, l.lesson_id ASC
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

    def begin_trash(self, entry: TrashEntry, operation_id: str) -> None:
        def operation() -> None:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT payload, deleted_at FROM lessons WHERE lesson_id=?",
                    (entry.lesson_id,),
                ).fetchone()
                if row is None:
                    raise ContentNotFoundError(f"Занятие не найдено: {entry.lesson_id}")
                if row["deleted_at"] is not None:
                    raise ContentConflictError(f"Занятие уже находится в корзине: {entry.lesson_id}")
                lesson = self._lesson_from_row(row)
                if lesson.status.value in {"recording", "transcribing"}:
                    raise ActiveLessonError("Нельзя удалить занятие во время записи или транскрибации")
                active_job = db.execute(
                    """
                    SELECT status FROM transcription_jobs
                    WHERE lesson_id=? AND status IN ('waiting', 'running')
                    """,
                    (entry.lesson_id,),
                ).fetchone()
                if active_job:
                    raise ActiveLessonError("Нельзя удалить занятие из активной очереди транскрибации")
                db.execute("DELETE FROM transcription_jobs WHERE lesson_id=?", (entry.lesson_id,))
                db.execute(
                    "UPDATE lessons SET deleted_at=? WHERE lesson_id=?",
                    (entry.deleted_at.isoformat(), entry.lesson_id),
                )
                db.execute(
                    """
                    INSERT INTO content_trash (
                        lesson_id, original_relative_path, trash_relative_path,
                        staging_relative_path, size_bytes, state, deleted_at, purge_after
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.lesson_id,
                        entry.original_relative_path,
                        entry.trash_relative_path,
                        entry.staging_relative_path,
                        entry.size_bytes,
                        entry.state.value,
                        entry.deleted_at.isoformat(),
                        entry.purge_after.isoformat(),
                    ),
                )
                self._insert_operation(
                    db,
                    operation_id,
                    entry.lesson_id,
                    ContentOperationKind.DELETE,
                    ContentOperationStatus.PENDING,
                    entry.original_relative_path,
                    entry.trash_relative_path,
                    entry.size_bytes,
                    entry.deleted_at,
                )

        self._retry(operation)

    @staticmethod
    def _insert_operation(
        db: sqlite3.Connection,
        operation_id: str,
        lesson_id: str,
        operation: ContentOperationKind,
        status: ContentOperationStatus,
        source: str | None,
        destination: str | None,
        size_bytes: int,
        created_at: datetime,
    ) -> None:
        db.execute(
            """
            INSERT INTO content_operations (
                id, lesson_id, operation, status, source_relative_path,
                destination_relative_path, size_bytes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                lesson_id,
                operation.value,
                status.value,
                source,
                destination,
                size_bytes,
                created_at.isoformat(),
            ),
        )

    def complete_trash(self, lesson_id: str, operation_id: str) -> None:
        def operation() -> None:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                cursor = db.execute(
                    "UPDATE content_trash SET state=? WHERE lesson_id=? AND state=?",
                    (TrashState.TRASHED.value, lesson_id, TrashState.MOVING.value),
                )
                if cursor.rowcount == 0:
                    raise ContentNotFoundError(f"Операция удаления не найдена: {lesson_id}")
                self._complete_operation(db, operation_id)

        self._retry(operation)

    def rollback_trash(self, lesson_id: str, operation_id: str, details: str) -> None:
        def operation() -> None:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE lessons SET deleted_at=NULL WHERE lesson_id=?", (lesson_id,))
                db.execute("DELETE FROM content_trash WHERE lesson_id=?", (lesson_id,))
                self._fail_operation(db, operation_id, details)

        self._retry(operation)

    def begin_restore(self, lesson_id: str, operation_id: str, created_at: datetime) -> TrashEntry:
        def operation() -> sqlite3.Row:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM content_trash WHERE lesson_id=? AND state=?",
                    (lesson_id, TrashState.TRASHED.value),
                ).fetchone()
                if row is None:
                    raise ContentNotFoundError(f"Занятие не найдено в корзине: {lesson_id}")
                db.execute(
                    "UPDATE content_trash SET state=? WHERE lesson_id=?",
                    (TrashState.RESTORING.value, lesson_id),
                )
                self._insert_operation(
                    db,
                    operation_id,
                    lesson_id,
                    ContentOperationKind.RESTORE,
                    ContentOperationStatus.PENDING,
                    str(row["trash_relative_path"]),
                    str(row["original_relative_path"]),
                    int(row["size_bytes"]),
                    created_at,
                )
                return row

        return self._trash_from_row(self._retry(operation))

    def complete_restore(self, lesson_id: str, operation_id: str) -> None:
        def operation() -> None:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE lessons SET deleted_at=NULL WHERE lesson_id=?", (lesson_id,))
                cursor = db.execute(
                    "DELETE FROM content_trash WHERE lesson_id=? AND state=?",
                    (lesson_id, TrashState.RESTORING.value),
                )
                if cursor.rowcount == 0:
                    raise ContentNotFoundError(f"Операция восстановления не найдена: {lesson_id}")
                self._complete_operation(db, operation_id)

        self._retry(operation)

    def rollback_restore(self, lesson_id: str, operation_id: str, details: str) -> None:
        def operation() -> None:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "UPDATE content_trash SET state=? WHERE lesson_id=?",
                    (TrashState.TRASHED.value, lesson_id),
                )
                self._fail_operation(db, operation_id, details)

        self._retry(operation)

    def begin_purge(
        self,
        lesson_id: str,
        operation_id: str,
        staging_relative_path: str,
        created_at: datetime,
    ) -> TrashEntry:
        def operation() -> sqlite3.Row:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM content_trash WHERE lesson_id=? AND state=?",
                    (lesson_id, TrashState.TRASHED.value),
                ).fetchone()
                if row is None:
                    raise ContentNotFoundError(f"Занятие не найдено в корзине: {lesson_id}")
                db.execute(
                    """
                    UPDATE content_trash SET state=?, staging_relative_path=?
                    WHERE lesson_id=?
                    """,
                    (TrashState.PURGING.value, staging_relative_path, lesson_id),
                )
                self._insert_operation(
                    db,
                    operation_id,
                    lesson_id,
                    ContentOperationKind.PURGE,
                    ContentOperationStatus.PENDING,
                    str(row["trash_relative_path"]),
                    staging_relative_path,
                    int(row["size_bytes"]),
                    created_at,
                )
                mutable = dict(row)
                mutable["state"] = TrashState.PURGING.value
                mutable["staging_relative_path"] = staging_relative_path
                return mutable

        row = self._retry(operation)
        return TrashEntry.model_validate(dict(row))

    def rollback_purge(self, lesson_id: str, operation_id: str, details: str) -> None:
        def operation() -> None:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """
                    UPDATE content_trash SET state=?, staging_relative_path=NULL
                    WHERE lesson_id=?
                    """,
                    (TrashState.TRASHED.value, lesson_id),
                )
                self._fail_operation(db, operation_id, details)

        self._retry(operation)

    def complete_purge_database(self, lesson_id: str, operation_id: str) -> None:
        def operation() -> None:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if not db.execute(
                    "SELECT 1 FROM content_trash WHERE lesson_id=? AND state=?",
                    (lesson_id, TrashState.PURGING.value),
                ).fetchone():
                    raise ContentNotFoundError(f"Операция очистки не найдена: {lesson_id}")
                db.execute("DELETE FROM transcription_jobs WHERE lesson_id=?", (lesson_id,))
                db.execute("DELETE FROM transcript_drafts WHERE lesson_id=?", (lesson_id,))
                db.execute("DELETE FROM transcript_revisions WHERE lesson_id=?", (lesson_id,))
                db.execute("DELETE FROM lesson_assets WHERE lesson_id=?", (lesson_id,))
                db.execute("DELETE FROM content_trash WHERE lesson_id=?", (lesson_id,))
                if self._fts_available(db):
                    db.execute("DELETE FROM lesson_search WHERE lesson_id=?", (lesson_id,))
                db.execute("DELETE FROM lessons WHERE lesson_id=?", (lesson_id,))
                db.execute(
                    "UPDATE content_operations SET status=? WHERE id=?",
                    (ContentOperationStatus.CLEANUP_PENDING.value, operation_id),
                )

        self._retry(operation)

    def complete_cleanup(self, operation_id: str) -> None:
        def operation() -> None:
            with self.connect() as db:
                self._complete_operation(db, operation_id)

        self._retry(operation)

    @staticmethod
    def _complete_operation(db: sqlite3.Connection, operation_id: str) -> None:
        cursor = db.execute(
            """
            UPDATE content_operations SET status=?, completed_at=? WHERE id=?
            """,
            (ContentOperationStatus.COMPLETED.value, datetime.now(UTC).isoformat(), operation_id),
        )
        if cursor.rowcount == 0:
            raise ContentNotFoundError(f"Операция не найдена: {operation_id}")

    @staticmethod
    def _fail_operation(db: sqlite3.Connection, operation_id: str, details: str) -> None:
        db.execute(
            """
            UPDATE content_operations SET status=?, details=?, completed_at=? WHERE id=?
            """,
            (
                ContentOperationStatus.FAILED.value,
                details,
                datetime.now(UTC).isoformat(),
                operation_id,
            ),
        )

    def list_trash_items(self) -> list[TrashItem]:
        def operation() -> list[sqlite3.Row]:
            with self.connect() as db:
                return db.execute(
                    """
                    SELECT l.payload, t.* FROM content_trash t
                    JOIN lessons l ON l.lesson_id=t.lesson_id
                    ORDER BY t.deleted_at DESC, t.lesson_id
                    """
                ).fetchall()

        return [
            TrashItem(lesson=self._lesson_from_row(row), entry=self._trash_from_row(row))
            for row in self._retry(operation)
        ]

    def reschedule_trash_purge(self, retention_days: int) -> None:
        def operation() -> None:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                rows = db.execute(
                    "SELECT lesson_id, deleted_at FROM content_trash WHERE state=?",
                    (TrashState.TRASHED.value,),
                ).fetchall()
                for row in rows:
                    purge_after = datetime.fromisoformat(str(row["deleted_at"])) + timedelta(
                        days=retention_days
                    )
                    db.execute(
                        "UPDATE content_trash SET purge_after=? WHERE lesson_id=?",
                        (purge_after.isoformat(), str(row["lesson_id"])),
                    )

        self._retry(operation)

    def get_trash_entry(self, lesson_id: str) -> TrashEntry | None:
        def operation() -> sqlite3.Row | None:
            with self.connect() as db:
                return db.execute(
                    "SELECT * FROM content_trash WHERE lesson_id=?",
                    (lesson_id,),
                ).fetchone()

        row = self._retry(operation)
        return self._trash_from_row(row) if row else None

    def list_incomplete_trash(self) -> list[TrashEntry]:
        def operation() -> list[sqlite3.Row]:
            with self.connect() as db:
                return db.execute(
                    "SELECT * FROM content_trash WHERE state<>? ORDER BY deleted_at",
                    (TrashState.TRASHED.value,),
                ).fetchall()

        return [self._trash_from_row(row) for row in self._retry(operation)]

    def pending_operation(self, lesson_id: str, kind: ContentOperationKind) -> ContentOperation:
        def operation() -> sqlite3.Row | None:
            with self.connect() as db:
                return db.execute(
                    """
                    SELECT * FROM content_operations
                    WHERE lesson_id=? AND operation=? AND status=?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (
                        lesson_id,
                        kind.value,
                        ContentOperationStatus.PENDING.value,
                    ),
                ).fetchone()

        row = self._retry(operation)
        if row is None:
            raise ContentNotFoundError(f"Незавершённая операция не найдена: {lesson_id}")
        return self._operation_from_row(row)

    def list_cleanup_operations(self) -> list[ContentOperation]:
        def operation() -> list[sqlite3.Row]:
            with self.connect() as db:
                return db.execute(
                    "SELECT * FROM content_operations WHERE status=? ORDER BY created_at",
                    (ContentOperationStatus.CLEANUP_PENDING.value,),
                ).fetchall()

        return [self._operation_from_row(row) for row in self._retry(operation)]

    def list_operations(self, *, limit: int = 200) -> list[ContentOperation]:
        def operation() -> list[sqlite3.Row]:
            with self.connect() as db:
                return db.execute(
                    "SELECT * FROM content_operations ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()

        return [self._operation_from_row(row) for row in self._retry(operation)]

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
                self._mark_file_sync(db, lesson.lesson_id)
                self._refresh_search_document(db, lesson.lesson_id)

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
                        updated_at=excluded.updated_at,
                        deleted_at=CASE
                            WHEN lesson_assets.deleted_at IS NOT NULL
                             AND lesson_assets.sha256=excluded.sha256
                             AND lesson_assets.size_bytes=excluded.size_bytes
                            THEN lesson_assets.deleted_at
                            ELSE excluded.deleted_at
                        END
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
                        self._refresh_search_document(db, revision.lesson_id)
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
                row = db.execute(
                    """
                    SELECT id, lesson_id, revision_number, relative_path, content,
                           content_sha256, created_by, created_at, deleted_at
                    FROM transcript_revisions WHERE id=?
                    """,
                    (cursor.lastrowid,),
                ).fetchone()
                self._refresh_search_document(db, revision.lesson_id)
                return row

        return self._revision_from_row(self._retry(operation))

    def commit_transcript_revision(
        self,
        revision: TranscriptRevision,
        *,
        expected_revision_number: int | None,
        verified_transcript: str,
    ) -> tuple[TranscriptRevision, Lesson]:
        """Append a revision and update lesson state under one optimistic transaction."""

        def operation() -> tuple[sqlite3.Row, Lesson]:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                lesson_row = db.execute(
                    "SELECT payload FROM lessons WHERE lesson_id=? AND deleted_at IS NULL",
                    (revision.lesson_id,),
                ).fetchone()
                if lesson_row is None:
                    raise ContentNotFoundError(f"Занятие не найдено: {revision.lesson_id}")
                current_number = db.execute(
                    "SELECT MAX(revision_number) FROM transcript_revisions WHERE lesson_id=?",
                    (revision.lesson_id,),
                ).fetchone()[0]
                current_number = int(current_number) if current_number is not None else None
                if current_number != expected_revision_number:
                    raise TranscriptEditConflictError(expected_revision_number, current_number)
                number = (current_number or 0) + 1
                cursor = db.execute(
                    """
                    INSERT INTO transcript_revisions (
                        lesson_id, revision_number, relative_path, content,
                        content_sha256, created_by, created_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        revision.lesson_id,
                        number,
                        revision.relative_path,
                        revision.content,
                        revision.content_sha256,
                        revision.created_by,
                        revision.created_at.isoformat(),
                    ),
                )
                lesson = self._lesson_from_row(lesson_row)
                lesson.artifacts.verified_transcript = verified_transcript
                lesson.mark_generated_materials_stale(transcript_revision=number)
                lesson.updated_at = datetime.now(UTC)
                db.execute(
                    """
                    UPDATE lessons SET status=?, payload=?, updated_at=?,
                        row_version=row_version + 1 WHERE lesson_id=?
                    """,
                    (
                        lesson.status.value,
                        lesson.model_dump_json(),
                        lesson.updated_at.isoformat(),
                        lesson.lesson_id,
                    ),
                )
                self._mark_file_sync(db, lesson.lesson_id)
                self._refresh_search_document(db, lesson.lesson_id)
                row = db.execute(
                    """
                    SELECT id, lesson_id, revision_number, relative_path, content,
                           content_sha256, created_by, created_at, deleted_at
                    FROM transcript_revisions WHERE id=?
                    """,
                    (cursor.lastrowid,),
                ).fetchone()
                return row, lesson

        row, lesson = self._retry(operation)
        return self._revision_from_row(row), lesson

    def save_transcript_draft(self, draft: TranscriptDraft) -> TranscriptDraft:
        def operation() -> sqlite3.Row:
            with self.connect() as db:
                if not db.execute(
                    "SELECT 1 FROM lessons WHERE lesson_id=? AND deleted_at IS NULL",
                    (draft.lesson_id,),
                ).fetchone():
                    raise ContentNotFoundError(f"Занятие не найдено: {draft.lesson_id}")
                db.execute(
                    """
                    INSERT INTO transcript_drafts (
                        lesson_id, base_revision_number, content, content_sha256, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(lesson_id) DO UPDATE SET
                        base_revision_number=excluded.base_revision_number,
                        content=excluded.content,
                        content_sha256=excluded.content_sha256,
                        updated_at=excluded.updated_at
                    """,
                    (
                        draft.lesson_id,
                        draft.base_revision_number,
                        draft.content,
                        draft.content_sha256,
                        draft.updated_at.isoformat(),
                    ),
                )
                return db.execute(
                    """
                    SELECT lesson_id, base_revision_number, content, content_sha256, updated_at
                    FROM transcript_drafts WHERE lesson_id=?
                    """,
                    (draft.lesson_id,),
                ).fetchone()

        return self._draft_from_row(self._retry(operation))

    def get_transcript_draft(self, lesson_id: str) -> TranscriptDraft | None:
        def operation() -> sqlite3.Row | None:
            with self.connect() as db:
                return db.execute(
                    """
                    SELECT lesson_id, base_revision_number, content, content_sha256, updated_at
                    FROM transcript_drafts WHERE lesson_id=?
                    """,
                    (lesson_id,),
                ).fetchone()

        row = self._retry(operation)
        return self._draft_from_row(row) if row else None

    def delete_transcript_draft(
        self,
        lesson_id: str,
        *,
        content_sha256: str | None = None,
        base_revision_number: int | None = None,
        conditional: bool = False,
    ) -> None:
        def operation() -> None:
            with self.connect() as db:
                if conditional:
                    db.execute(
                        """
                        DELETE FROM transcript_drafts
                        WHERE lesson_id=? AND content_sha256=?
                          AND base_revision_number IS ?
                        """,
                        (lesson_id, content_sha256, base_revision_number),
                    )
                else:
                    db.execute("DELETE FROM transcript_drafts WHERE lesson_id=?", (lesson_id,))

        self._retry(operation)

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
                row = db.execute(
                    "SELECT lesson_id FROM transcript_revisions WHERE id=?",
                    (revision_id,),
                ).fetchone()
                if row is None:
                    raise ContentNotFoundError(f"Версия транскрипта не найдена: {revision_id}")
                cursor = db.execute(
                    "UPDATE transcript_revisions SET deleted_at=? WHERE id=?",
                    (timestamp, revision_id),
                )
                if cursor.rowcount == 0:
                    raise ContentNotFoundError(f"Версия транскрипта не найдена: {revision_id}")
                self._refresh_search_document(db, str(row["lesson_id"]))

        self._retry(operation)

    def rebuild_search_index(self) -> int:
        def operation() -> int:
            with self.connect() as db:
                if not self._fts_available(db):
                    return 0
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM lesson_search")
                lesson_ids = [
                    str(row["lesson_id"])
                    for row in db.execute("SELECT lesson_id FROM lessons ORDER BY lesson_id")
                ]
                for lesson_id in lesson_ids:
                    self._refresh_search_document(db, lesson_id)
                return len(lesson_ids)

        return self._retry(operation)

    def search_index_status(self) -> tuple[bool, int]:
        def operation() -> tuple[bool, int]:
            with self.connect() as db:
                enabled = self._fts_available(db)
                count = int(db.execute("SELECT COUNT(*) FROM lesson_search").fetchone()[0]) if enabled else 0
                return enabled, count

        return self._retry(operation)

    def search_index_mismatches(self) -> list[tuple[str, str]]:
        """Return lesson ids whose FTS projection is missing, stale or orphaned."""

        def operation() -> list[tuple[str, str]]:
            with self.connect() as db:
                if not self._fts_available(db):
                    return []
                mismatches: list[tuple[str, str]] = []
                rows = db.execute(
                    """
                    SELECT l.lesson_id, l.payload AS expected_metadata,
                           COALESCE((
                               SELECT r.content FROM transcript_revisions r
                               WHERE r.lesson_id=l.lesson_id AND r.deleted_at IS NULL
                               ORDER BY r.revision_number DESC LIMIT 1
                           ), '') AS expected_transcript,
                           s.metadata AS indexed_metadata,
                           s.transcript AS indexed_transcript
                    FROM lessons l
                    LEFT JOIN lesson_search s ON s.lesson_id=l.lesson_id
                    ORDER BY l.lesson_id
                    """
                ).fetchall()
                for row in rows:
                    lesson_id = str(row["lesson_id"])
                    if row["indexed_metadata"] is None:
                        mismatches.append((lesson_id, "missing"))
                    elif str(row["expected_metadata"]) != str(row["indexed_metadata"]) or str(
                        row["expected_transcript"]
                    ) != str(row["indexed_transcript"]):
                        mismatches.append((lesson_id, "stale"))
                extra = db.execute(
                    """
                    SELECT s.lesson_id FROM lesson_search s
                    LEFT JOIN lessons l ON l.lesson_id=s.lesson_id
                    WHERE l.lesson_id IS NULL
                    ORDER BY s.lesson_id
                    """
                ).fetchall()
                mismatches.extend((str(row["lesson_id"]), "orphan") for row in extra)
                return mismatches

        return self._retry(operation)

    def database_integrity_status(self) -> tuple[bool, str]:
        def operation() -> tuple[bool, str]:
            with self.connect() as db:
                quick = [str(row[0]) for row in db.execute("PRAGMA quick_check").fetchall()]
                foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
                messages = [item for item in quick if item.casefold() != "ok"]
                if foreign_keys:
                    messages.append(f"нарушений внешних ключей: {len(foreign_keys)}")
                return not messages, "; ".join(messages) or "ok"

        return self._retry(operation)

    def list_lesson_index_states(self) -> list[tuple[str, bool]]:
        def operation() -> list[sqlite3.Row]:
            with self.connect() as db:
                return db.execute("SELECT lesson_id, deleted_at FROM lessons ORDER BY lesson_id").fetchall()

        return [(str(row["lesson_id"]), row["deleted_at"] is not None) for row in self._retry(operation)]

    def protected_temporary_paths(self) -> set[str]:
        def operation() -> list[sqlite3.Row]:
            with self.connect() as db:
                return db.execute(
                    """
                    SELECT destination_relative_path FROM content_operations
                    WHERE operation=? AND status IN (?, ?)
                      AND destination_relative_path IS NOT NULL
                    """,
                    (
                        ContentOperationKind.PURGE.value,
                        ContentOperationStatus.PENDING.value,
                        ContentOperationStatus.CLEANUP_PENDING.value,
                    ),
                ).fetchall()

        return {str(row[0]) for row in self._retry(operation)}

    def applied_migrations(self) -> list[tuple[int, str]]:
        with self.connect() as db:
            rows = db.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
        return [(int(row["version"]), str(row["name"])) for row in rows]
