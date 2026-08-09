from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .crm import CrmStore, ScheduledLesson


class AttendanceStatus(StrEnum):
    UNKNOWN = "unknown"
    PRESENT = "present"
    LATE = "late"
    NO_SHOW = "no_show"
    EXCUSED = "excused"


ATTENDANCE_LABELS = {
    AttendanceStatus.UNKNOWN: "Не отмечено",
    AttendanceStatus.PRESENT: "Был",
    AttendanceStatus.LATE: "Опоздал",
    AttendanceStatus.NO_SHOW: "Не пришёл",
    AttendanceStatus.EXCUSED: "По договорённости",
}


@dataclass(frozen=True, slots=True)
class LessonCloseoutMeta:
    occurrence_id: int
    attendance: AttendanceStatus = AttendanceStatus.UNKNOWN
    teacher_note: str = ""
    closed_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LessonCloseoutSnapshot:
    occurrence_id: int | None
    occurrence_existed: bool
    lesson_status: str
    closeout_existed: bool
    attendance: AttendanceStatus = AttendanceStatus.UNKNOWN
    teacher_note: str = ""
    closed_at: datetime | None = None
    updated_at: datetime | None = None


class LessonCloseoutService:
    """Local pedagogical closeout state for a concrete lesson occurrence."""

    def __init__(self, crm_store: CrmStore) -> None:
        self.crm_store = crm_store
        self._initialize()

    def _initialize(self) -> None:
        with self.crm_store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS crm_lesson_closeout (
                    occurrence_id INTEGER PRIMARY KEY,
                    attendance TEXT NOT NULL DEFAULT 'unknown',
                    teacher_note_secret TEXT,
                    closed_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(occurrence_id)
                        REFERENCES crm_lesson_occurrences(id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS crm_closeout_attendance
                    ON crm_lesson_closeout(attendance);
                CREATE INDEX IF NOT EXISTS crm_closeout_closed
                    ON crm_lesson_closeout(closed_at);
                """
            )

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        text = str(value).strip()
        return datetime.fromisoformat(text) if text else None

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat(timespec="seconds") if value is not None else None

    def _meta_from_row(self, row: sqlite3.Row) -> LessonCloseoutMeta:
        try:
            attendance = AttendanceStatus(str(row["attendance"]))
        except ValueError:
            attendance = AttendanceStatus.UNKNOWN
        return LessonCloseoutMeta(
            occurrence_id=int(row["occurrence_id"]),
            attendance=attendance,
            teacher_note=self.crm_store.codec.decrypt(row["teacher_note_secret"]) or "",
            closed_at=self._parse_datetime(row["closed_at"]),
            updated_at=self._parse_datetime(row["updated_at"]),
        )

    def list_for_occurrences(
        self,
        occurrence_ids: set[int],
    ) -> dict[int, LessonCloseoutMeta]:
        if not occurrence_ids:
            return {}
        placeholders = ",".join("?" for _ in occurrence_ids)
        with self.crm_store.connect() as db:
            rows = db.execute(
                f"""
                SELECT occurrence_id, attendance, teacher_note_secret, closed_at, updated_at
                FROM crm_lesson_closeout
                WHERE occurrence_id IN ({placeholders})
                """,  # noqa: S608
                tuple(sorted(occurrence_ids)),
            ).fetchall()
        return {int(row["occurrence_id"]): self._meta_from_row(row) for row in rows}

    def _existing_occurrence_id(
        self,
        db: sqlite3.Connection,
        lesson: ScheduledLesson,
    ) -> int | None:
        if lesson.occurrence_id is not None:
            row = db.execute(
                "SELECT id FROM crm_lesson_occurrences WHERE id=?",
                (lesson.occurrence_id,),
            ).fetchone()
            return int(row["id"]) if row else None
        if lesson.rule_id is not None:
            original_date = lesson.original_date or lesson.starts_at.date()
            row = db.execute(
                """
                SELECT id
                FROM crm_lesson_occurrences
                WHERE rule_id=? AND original_date=?
                """,
                (lesson.rule_id, original_date.isoformat()),
            ).fetchone()
            return int(row["id"]) if row else None
        row = db.execute(
            """
            SELECT id
            FROM crm_lesson_occurrences
            WHERE student_id=? AND starts_at=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (lesson.student_id, lesson.starts_at.isoformat()),
        ).fetchone()
        return int(row["id"]) if row else None

    def _ensure_occurrence(
        self,
        db: sqlite3.Connection,
        lesson: ScheduledLesson,
        *,
        now: str,
    ) -> int:
        existing = self._existing_occurrence_id(db, lesson)
        if existing is not None:
            return existing
        original_date = lesson.original_date or lesson.starts_at.date()
        if lesson.rule_id is not None:
            db.execute(
                """
                INSERT INTO crm_lesson_occurrences (
                    rule_id, original_date, student_id, starts_at, duration_minutes, subject,
                    topic, meeting_secret, status, rate_cents, paid, lesson_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rule_id, original_date) DO NOTHING
                """,
                (
                    lesson.rule_id,
                    original_date.isoformat(),
                    lesson.student_id,
                    lesson.starts_at.isoformat(),
                    lesson.duration_minutes,
                    lesson.subject,
                    lesson.topic,
                    self.crm_store.codec.encrypt(lesson.meeting_url),
                    lesson.status,
                    lesson.rate_cents,
                    int(lesson.paid),
                    lesson.lesson_id,
                    now,
                    now,
                ),
            )
            occurrence_id = self._existing_occurrence_id(db, lesson)
            if occurrence_id is None:
                raise RuntimeError("Не удалось материализовать повторяющееся занятие")
            return occurrence_id
        cursor = db.execute(
            """
            INSERT INTO crm_lesson_occurrences (
                rule_id, original_date, student_id, starts_at, duration_minutes, subject,
                topic, meeting_secret, status, rate_cents, paid, lesson_id,
                created_at, updated_at
            ) VALUES (NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lesson.student_id,
                lesson.starts_at.isoformat(),
                lesson.duration_minutes,
                lesson.subject,
                lesson.topic,
                self.crm_store.codec.encrypt(lesson.meeting_url),
                lesson.status,
                lesson.rate_cents,
                int(lesson.paid),
                lesson.lesson_id,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def get_for_lesson(self, lesson: ScheduledLesson) -> LessonCloseoutMeta | None:
        with self.crm_store.connect() as db:
            occurrence_id = self._existing_occurrence_id(db, lesson)
            if occurrence_id is None:
                return None
            row = db.execute(
                """
                SELECT occurrence_id, attendance, teacher_note_secret, closed_at, updated_at
                FROM crm_lesson_closeout
                WHERE occurrence_id=?
                """,
                (occurrence_id,),
            ).fetchone()
        return self._meta_from_row(row) if row else None

    def snapshot(self, lesson: ScheduledLesson) -> LessonCloseoutSnapshot:
        with self.crm_store.connect() as db:
            occurrence_id = self._existing_occurrence_id(db, lesson)
            if occurrence_id is None:
                return LessonCloseoutSnapshot(
                    occurrence_id=None,
                    occurrence_existed=False,
                    lesson_status=lesson.status,
                    closeout_existed=False,
                )
            occurrence = db.execute(
                "SELECT status FROM crm_lesson_occurrences WHERE id=?",
                (occurrence_id,),
            ).fetchone()
            row = db.execute(
                """
                SELECT occurrence_id, attendance, teacher_note_secret, closed_at, updated_at
                FROM crm_lesson_closeout
                WHERE occurrence_id=?
                """,
                (occurrence_id,),
            ).fetchone()
        status = str(occurrence["status"]) if occurrence else lesson.status
        if row is None:
            return LessonCloseoutSnapshot(
                occurrence_id=occurrence_id,
                occurrence_existed=True,
                lesson_status=status,
                closeout_existed=False,
            )
        meta = self._meta_from_row(row)
        return LessonCloseoutSnapshot(
            occurrence_id=occurrence_id,
            occurrence_existed=True,
            lesson_status=status,
            closeout_existed=True,
            attendance=meta.attendance,
            teacher_note=meta.teacher_note,
            closed_at=meta.closed_at,
            updated_at=meta.updated_at,
        )

    def _write_closeout(
        self,
        db: sqlite3.Connection,
        *,
        occurrence_id: int,
        attendance: AttendanceStatus,
        teacher_note: str,
        closed_at: datetime | None,
        updated_at: str,
    ) -> None:
        db.execute(
            """
            INSERT INTO crm_lesson_closeout (
                occurrence_id, attendance, teacher_note_secret, closed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(occurrence_id) DO UPDATE SET
                attendance=excluded.attendance,
                teacher_note_secret=excluded.teacher_note_secret,
                closed_at=excluded.closed_at,
                updated_at=excluded.updated_at
            """,
            (
                occurrence_id,
                attendance.value,
                self.crm_store.codec.encrypt(teacher_note),
                self._iso(closed_at),
                updated_at,
            ),
        )

    def save_draft(
        self,
        lesson: ScheduledLesson,
        *,
        attendance: AttendanceStatus | str,
        teacher_note: str,
        at: datetime | None = None,
    ) -> int:
        parsed = AttendanceStatus(attendance)
        current_time = at or datetime.now()
        now = current_time.isoformat(timespec="seconds")
        with self.crm_store.connect() as db:
            occurrence_id = self._ensure_occurrence(db, lesson, now=now)
            existing = db.execute(
                "SELECT closed_at FROM crm_lesson_closeout WHERE occurrence_id=?",
                (occurrence_id,),
            ).fetchone()
            closed_at = self._parse_datetime(existing["closed_at"]) if existing else None
            self._write_closeout(
                db,
                occurrence_id=occurrence_id,
                attendance=parsed,
                teacher_note=teacher_note.strip(),
                closed_at=closed_at,
                updated_at=now,
            )
        return occurrence_id

    def set_attendance(
        self,
        lesson: ScheduledLesson,
        attendance: AttendanceStatus | str,
        *,
        at: datetime | None = None,
    ) -> int:
        parsed = AttendanceStatus(attendance)
        current_time = at or datetime.now()
        now = current_time.isoformat(timespec="seconds")
        with self.crm_store.connect() as db:
            occurrence_id = self._ensure_occurrence(db, lesson, now=now)
            row = db.execute(
                """
                SELECT teacher_note_secret, closed_at
                FROM crm_lesson_closeout
                WHERE occurrence_id=?
                """,
                (occurrence_id,),
            ).fetchone()
            teacher_note = (
                self.crm_store.codec.decrypt(row["teacher_note_secret"]) or ""
                if row
                else ""
            )
            closed_at = self._parse_datetime(row["closed_at"]) if row else None
            self._write_closeout(
                db,
                occurrence_id=occurrence_id,
                attendance=parsed,
                teacher_note=teacher_note,
                closed_at=closed_at,
                updated_at=now,
            )
        return occurrence_id

    def close_lesson(
        self,
        lesson: ScheduledLesson,
        *,
        attendance: AttendanceStatus | str,
        teacher_note: str,
        at: datetime | None = None,
    ) -> int:
        if lesson.status == "cancelled":
            raise ValueError("Отменённое занятие нельзя завершить")
        parsed = AttendanceStatus(attendance)
        if parsed == AttendanceStatus.UNKNOWN:
            raise ValueError("Укажите посещаемость перед завершением занятия")
        current_time = at or datetime.now()
        if lesson.ends_at > current_time:
            raise ValueError("Занятие ещё не закончилось")
        now = current_time.isoformat(timespec="seconds")
        with self.crm_store.connect() as db:
            occurrence_id = self._ensure_occurrence(db, lesson, now=now)
            db.execute(
                """
                UPDATE crm_lesson_occurrences
                SET status='completed', updated_at=?
                WHERE id=?
                """,
                (now, occurrence_id),
            )
            self._write_closeout(
                db,
                occurrence_id=occurrence_id,
                attendance=parsed,
                teacher_note=teacher_note.strip(),
                closed_at=current_time,
                updated_at=now,
            )
        return occurrence_id

    def restore_snapshot(
        self,
        lesson: ScheduledLesson,
        snapshot: LessonCloseoutSnapshot,
        *,
        at: datetime | None = None,
    ) -> int:
        current_time = at or datetime.now()
        now = current_time.isoformat(timespec="seconds")
        with self.crm_store.connect() as db:
            occurrence_id = snapshot.occurrence_id
            if occurrence_id is None:
                occurrence_id = self._existing_occurrence_id(db, lesson)
            if occurrence_id is None:
                occurrence_id = self._ensure_occurrence(db, lesson, now=now)
            db.execute(
                """
                UPDATE crm_lesson_occurrences
                SET status=?, updated_at=?
                WHERE id=?
                """,
                (snapshot.lesson_status, now, occurrence_id),
            )
            if snapshot.closeout_existed:
                self._write_closeout(
                    db,
                    occurrence_id=occurrence_id,
                    attendance=snapshot.attendance,
                    teacher_note=snapshot.teacher_note,
                    closed_at=snapshot.closed_at,
                    updated_at=self._iso(snapshot.updated_at) or now,
                )
            else:
                db.execute(
                    "DELETE FROM crm_lesson_closeout WHERE occurrence_id=?",
                    (occurrence_id,),
                )
        return occurrence_id
