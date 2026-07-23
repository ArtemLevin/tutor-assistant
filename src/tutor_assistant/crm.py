from __future__ import annotations

import base64
import ctypes
import json
import logging
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from time import sleep
from typing import Protocol

from pydantic import BaseModel, Field, field_validator

from .content.coordination import (
    ActivityLease,
    ActivityLeaseStore,
    ContentBusyError,
    process_owner_id,
)
from .domain import Student
from .sqlite_utils import ClosingConnection


class SecretCodec(Protocol):
    def encrypt(self, value: str | None) -> str | None: ...

    def decrypt(self, value: str | None) -> str | None: ...


class PlainSecretCodec:
    """Development fallback for platforms without Windows DPAPI."""

    prefix = "plain:"

    def encrypt(self, value: str | None) -> str | None:
        return None if value is None else self.prefix + value

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return value.removeprefix(self.prefix)


if sys.platform == "win32":

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class WindowsDpapiCodec:
    prefix = "dpapi:"

    @staticmethod
    def _crypt(data: bytes, *, protect: bool) -> bytes:
        if sys.platform != "win32":
            raise RuntimeError("Windows DPAPI доступен только в Windows")
        buffer = ctypes.create_string_buffer(data)
        source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        result = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
        if protect:
            ok = crypt32.CryptProtectData(
                ctypes.byref(source),
                "Tutor Assistant CRM",
                None,
                None,
                None,
                flags,
                ctypes.byref(result),
            )
        else:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(source),
                None,
                None,
                None,
                None,
                flags,
                ctypes.byref(result),
            )
        if not ok:
            raise OSError(ctypes.get_last_error(), "Windows DPAPI operation failed")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            kernel32.LocalFree(result.pbData)

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        protected = self._crypt(value.encode("utf-8"), protect=True)
        return self.prefix + base64.urlsafe_b64encode(protected).decode("ascii")

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(self.prefix):
            return value.removeprefix(PlainSecretCodec.prefix)
        protected = base64.urlsafe_b64decode(value.removeprefix(self.prefix))
        return self._crypt(protected, protect=False).decode("utf-8")


def create_secret_codec() -> SecretCodec:
    if sys.platform == "win32":
        return WindowsDpapiCodec()
    logging.warning("CRM secrets use a plaintext development codec outside Windows")
    return PlainSecretCodec()


class Guardian(BaseModel):
    id: int | None = None
    full_name: str
    relationship: str = "Родитель"
    phone: str = ""
    email: str = ""
    social_url: str = ""
    preferred_contact: str = "phone"
    is_primary: bool = False


class StudentProfile(BaseModel):
    id: str
    full_name: str
    grade: int | None = Field(default=None, ge=1, le=12)
    school: str = ""
    goal: str = ""
    exam: str = ""
    target_score: int | None = Field(default=None, ge=0, le=100)
    subjects: list[str] = Field(default_factory=list)
    timezone: str = "Europe/Moscow"
    repository_folder: str | None = None
    default_rate_cents: int = Field(default=0, ge=0)
    currency: str = "RUB"
    notes: str = ""
    active: bool = True

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return Student(id=value, full_name="Temporary").id

    def to_student(self) -> Student:
        return Student(
            id=self.id,
            full_name=self.full_name,
            grade=self.grade,
            exam=self.exam or None,
            subjects=self.subjects,
            repository_folder=self.repository_folder,
        )


class ScheduleRule(BaseModel):
    id: int | None = None
    student_id: str
    weekday: int = Field(ge=0, le=6)
    start_minute: int = Field(ge=0, lt=1440)
    duration_minutes: int = Field(default=60, ge=15, le=360)
    subject: str = "mathematics"
    topic: str = ""
    meeting_url: str = ""
    valid_from: date
    valid_until: date | None = None
    rate_cents: int = Field(default=0, ge=0)
    active: bool = True


class ScheduledLesson(BaseModel):
    occurrence_id: int | None = None
    rule_id: int | None = None
    original_date: date | None = None
    student_id: str
    student_name: str
    starts_at: datetime
    duration_minutes: int
    subject: str
    topic: str = ""
    meeting_url: str = ""
    status: str = "planned"
    rate_cents: int = 0
    lesson_id: str | None = None

    @property
    def ends_at(self) -> datetime:
        return self.starts_at + timedelta(minutes=self.duration_minutes)


class ScheduleConflict(ValueError):
    pass


@dataclass(frozen=True)
class CrmStats:
    active_students: int
    lessons_this_week: int
    planned_revenue_cents: int


class CrmStore:
    def __init__(self, path: Path, codec: SecretCodec | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.codec = codec or create_secret_codec()
        self.lease_store = ActivityLeaseStore(path.parent / ".operations.sqlite3")
        self.owner_id = process_owner_id()
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        info = self.lease_store.acquire(
            owner_id=self.owner_id,
            activity="crm-access",
        )
        if info is None:
            raise ContentBusyError("Хранилище занято эксклюзивной операцией")
        lease = ActivityLease(self.lease_store, info, timedelta(minutes=2))
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA synchronous=NORMAL")
            with connection:
                yield connection
        finally:
            if connection is not None:
                connection.close()
            lease.release()

    @staticmethod
    def _retry(operation):
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
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS crm_students (
                    id TEXT PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    grade INTEGER,
                    school TEXT NOT NULL DEFAULT '',
                    goal TEXT NOT NULL DEFAULT '',
                    exam TEXT NOT NULL DEFAULT '',
                    target_score INTEGER,
                    subjects_json TEXT NOT NULL DEFAULT '[]',
                    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
                    repository_folder TEXT,
                    default_rate_cents INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'RUB',
                    notes_secret TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS crm_guardians (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    relationship TEXT NOT NULL DEFAULT 'Родитель',
                    phone_secret TEXT,
                    email_secret TEXT,
                    social_secret TEXT,
                    preferred_contact TEXT NOT NULL DEFAULT 'phone',
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(student_id) REFERENCES crm_students(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS crm_schedule_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL,
                    weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
                    start_minute INTEGER NOT NULL CHECK(start_minute BETWEEN 0 AND 1439),
                    duration_minutes INTEGER NOT NULL,
                    subject TEXT NOT NULL,
                    topic TEXT NOT NULL DEFAULT '',
                    meeting_secret TEXT,
                    valid_from TEXT NOT NULL,
                    valid_until TEXT,
                    rate_cents INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(student_id) REFERENCES crm_students(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS crm_lesson_occurrences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id INTEGER,
                    original_date TEXT,
                    student_id TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    subject TEXT NOT NULL,
                    topic TEXT NOT NULL DEFAULT '',
                    meeting_secret TEXT,
                    status TEXT NOT NULL DEFAULT 'planned',
                    rate_cents INTEGER NOT NULL DEFAULT 0,
                    lesson_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(rule_id) REFERENCES crm_schedule_rules(id) ON DELETE SET NULL,
                    FOREIGN KEY(student_id) REFERENCES crm_students(id) ON DELETE CASCADE,
                    UNIQUE(rule_id, original_date)
                );

                CREATE INDEX IF NOT EXISTS crm_schedule_week
                    ON crm_schedule_rules(weekday, active);
                CREATE INDEX IF NOT EXISTS crm_occurrences_start
                    ON crm_lesson_occurrences(starts_at);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def sync_students(self, students: Iterable[Student]) -> None:
        now = self._now()
        with self.connect() as db:
            for student in students:
                db.execute(
                    """
                    INSERT INTO crm_students (
                        id, full_name, grade, exam, subjects_json, repository_folder,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        repository_folder=COALESCE(
                            crm_students.repository_folder, excluded.repository_folder
                        )
                    """,
                    (
                        student.id,
                        student.full_name,
                        student.grade,
                        student.exam or "",
                        json.dumps(student.subjects, ensure_ascii=False),
                        student.repository_folder,
                        now,
                        now,
                    ),
                )

    def save_student(self, profile: StudentProfile, guardians: list[Guardian]) -> None:
        now = self._now()

        def operation() -> None:
            with self.connect() as db:
                db.execute(
                    """
                    INSERT INTO crm_students VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        full_name=excluded.full_name,
                        grade=excluded.grade,
                        school=excluded.school,
                        goal=excluded.goal,
                        exam=excluded.exam,
                        target_score=excluded.target_score,
                        subjects_json=excluded.subjects_json,
                        timezone=excluded.timezone,
                        repository_folder=excluded.repository_folder,
                        default_rate_cents=excluded.default_rate_cents,
                        currency=excluded.currency,
                        notes_secret=excluded.notes_secret,
                        active=excluded.active,
                        updated_at=excluded.updated_at
                    """,
                    (
                        profile.id,
                        profile.full_name,
                        profile.grade,
                        profile.school,
                        profile.goal,
                        profile.exam,
                        profile.target_score,
                        json.dumps(profile.subjects, ensure_ascii=False),
                        profile.timezone,
                        profile.repository_folder,
                        profile.default_rate_cents,
                        profile.currency,
                        self.codec.encrypt(profile.notes),
                        int(profile.active),
                        now,
                        now,
                    ),
                )
                db.execute("DELETE FROM crm_guardians WHERE student_id=?", (profile.id,))
                db.executemany(
                    """
                    INSERT INTO crm_guardians (
                        student_id, full_name, relationship, phone_secret, email_secret,
                        social_secret, preferred_contact, is_primary
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            profile.id,
                            guardian.full_name,
                            guardian.relationship,
                            self.codec.encrypt(guardian.phone),
                            self.codec.encrypt(guardian.email),
                            self.codec.encrypt(guardian.social_url),
                            guardian.preferred_contact,
                            int(guardian.is_primary),
                        )
                        for guardian in guardians
                    ],
                )

        self._retry(operation)

    def _profile_from_row(self, row: sqlite3.Row) -> StudentProfile:
        return StudentProfile(
            id=row["id"],
            full_name=row["full_name"],
            grade=row["grade"],
            school=row["school"],
            goal=row["goal"],
            exam=row["exam"],
            target_score=row["target_score"],
            subjects=json.loads(row["subjects_json"]),
            timezone=row["timezone"],
            repository_folder=row["repository_folder"],
            default_rate_cents=row["default_rate_cents"],
            currency=row["currency"],
            notes=self.codec.decrypt(row["notes_secret"]) or "",
            active=bool(row["active"]),
        )

    def list_students(self, *, include_archived: bool = False) -> list[StudentProfile]:
        where = "" if include_archived else "WHERE active=1"
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM crm_students {where} ORDER BY full_name COLLATE NOCASE"  # noqa: S608
            ).fetchall()
        return [self._profile_from_row(row) for row in rows]

    def get_student(self, student_id: str) -> StudentProfile | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM crm_students WHERE id=?", (student_id,)).fetchone()
        return self._profile_from_row(row) if row else None

    def list_guardians(self, student_id: str) -> list[Guardian]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM crm_guardians WHERE student_id=? ORDER BY is_primary DESC, id",
                (student_id,),
            ).fetchall()
        return [
            Guardian(
                id=row["id"],
                full_name=row["full_name"],
                relationship=row["relationship"],
                phone=self.codec.decrypt(row["phone_secret"]) or "",
                email=self.codec.decrypt(row["email_secret"]) or "",
                social_url=self.codec.decrypt(row["social_secret"]) or "",
                preferred_contact=row["preferred_contact"],
                is_primary=bool(row["is_primary"]),
            )
            for row in rows
        ]

    def domain_students(self) -> list[Student]:
        return [profile.to_student() for profile in self.list_students()]

    def archive_student(self, student_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE crm_students SET active=0, updated_at=? WHERE id=?",
                (self._now(), student_id),
            )

    @staticmethod
    def _overlaps(start_a: int, duration_a: int, start_b: int, duration_b: int) -> bool:
        return start_a < start_b + duration_b and start_b < start_a + duration_a

    def _check_rule_conflict(self, rule: ScheduleRule) -> None:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT id, start_minute, duration_minutes
                FROM crm_schedule_rules
                WHERE active=1 AND weekday=? AND (? IS NULL OR id<>?)
                """,
                (rule.weekday, rule.id, rule.id),
            ).fetchall()
        for row in rows:
            if self._overlaps(
                rule.start_minute,
                rule.duration_minutes,
                row["start_minute"],
                row["duration_minutes"],
            ):
                raise ScheduleConflict("В выбранное время уже назначено повторяющееся занятие")

    def save_schedule_rule(self, rule: ScheduleRule) -> int:
        self._check_rule_conflict(rule)
        now = self._now()
        with self.connect() as db:
            if rule.id is None:
                cursor = db.execute(
                    """
                    INSERT INTO crm_schedule_rules (
                        student_id, weekday, start_minute, duration_minutes, subject, topic,
                        meeting_secret, valid_from, valid_until, rate_cents, active,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rule.student_id,
                        rule.weekday,
                        rule.start_minute,
                        rule.duration_minutes,
                        rule.subject,
                        rule.topic,
                        self.codec.encrypt(rule.meeting_url),
                        rule.valid_from.isoformat(),
                        rule.valid_until.isoformat() if rule.valid_until else None,
                        rule.rate_cents,
                        int(rule.active),
                        now,
                        now,
                    ),
                )
                return int(cursor.lastrowid)
            db.execute(
                """
                UPDATE crm_schedule_rules SET
                    student_id=?, weekday=?, start_minute=?, duration_minutes=?, subject=?,
                    topic=?, meeting_secret=?, valid_from=?, valid_until=?, rate_cents=?,
                    active=?, updated_at=?
                WHERE id=?
                """,
                (
                    rule.student_id,
                    rule.weekday,
                    rule.start_minute,
                    rule.duration_minutes,
                    rule.subject,
                    rule.topic,
                    self.codec.encrypt(rule.meeting_url),
                    rule.valid_from.isoformat(),
                    rule.valid_until.isoformat() if rule.valid_until else None,
                    rule.rate_cents,
                    int(rule.active),
                    now,
                    rule.id,
                ),
            )
            return rule.id

    def delete_schedule_rule(self, rule_id: int) -> None:
        with self.connect() as db:
            db.execute("UPDATE crm_schedule_rules SET active=0 WHERE id=?", (rule_id,))

    def _rule_from_row(self, row: sqlite3.Row) -> ScheduleRule:
        return ScheduleRule(
            id=row["id"],
            student_id=row["student_id"],
            weekday=row["weekday"],
            start_minute=row["start_minute"],
            duration_minutes=row["duration_minutes"],
            subject=row["subject"],
            topic=row["topic"],
            meeting_url=self.codec.decrypt(row["meeting_secret"]) or "",
            valid_from=date.fromisoformat(row["valid_from"]),
            valid_until=date.fromisoformat(row["valid_until"]) if row["valid_until"] else None,
            rate_cents=row["rate_cents"],
            active=bool(row["active"]),
        )

    def list_schedule_rules(self) -> list[ScheduleRule]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM crm_schedule_rules WHERE active=1 ORDER BY weekday, start_minute"
            ).fetchall()
        return [self._rule_from_row(row) for row in rows]

    def save_one_off(self, lesson: ScheduledLesson) -> int:
        self._check_occurrence_conflict(lesson)
        now = self._now()
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO crm_lesson_occurrences (
                    rule_id, original_date, student_id, starts_at, duration_minutes, subject,
                    topic, meeting_secret, status, rate_cents, lesson_id, created_at, updated_at
                ) VALUES (NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lesson.student_id,
                    lesson.starts_at.isoformat(),
                    lesson.duration_minutes,
                    lesson.subject,
                    lesson.topic,
                    self.codec.encrypt(lesson.meeting_url),
                    lesson.status,
                    lesson.rate_cents,
                    lesson.lesson_id,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def _check_occurrence_conflict(
        self, lesson: ScheduledLesson, *, exclude_occurrence_id: int | None = None
    ) -> None:
        monday = lesson.starts_at.date() - timedelta(days=lesson.starts_at.weekday())
        for existing in self.lessons_for_week(monday):
            if existing.status == "cancelled" or existing.occurrence_id == exclude_occurrence_id:
                continue
            if lesson.starts_at < existing.ends_at and existing.starts_at < lesson.ends_at:
                raise ScheduleConflict(
                    f"В выбранное время уже назначено занятие с {existing.student_name}"
                )

    def update_occurrence_details(self, occurrence_id: int, lesson: ScheduledLesson) -> None:
        self._check_occurrence_conflict(lesson, exclude_occurrence_id=occurrence_id)
        with self.connect() as db:
            db.execute(
                """
                UPDATE crm_lesson_occurrences SET
                    student_id=?, starts_at=?, duration_minutes=?, subject=?, topic=?,
                    meeting_secret=?, rate_cents=?, updated_at=?
                WHERE id=?
                """,
                (
                    lesson.student_id,
                    lesson.starts_at.isoformat(),
                    lesson.duration_minutes,
                    lesson.subject,
                    lesson.topic,
                    self.codec.encrypt(lesson.meeting_url),
                    lesson.rate_cents,
                    self._now(),
                    occurrence_id,
                ),
            )

    def ensure_occurrence(self, lesson: ScheduledLesson) -> int:
        if lesson.occurrence_id is not None:
            return lesson.occurrence_id
        if lesson.rule_id is None:
            return self.save_one_off(lesson)
        now = self._now()
        original_date = lesson.original_date or lesson.starts_at.date()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO crm_lesson_occurrences (
                    rule_id, original_date, student_id, starts_at, duration_minutes, subject,
                    topic, meeting_secret, status, rate_cents, lesson_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    self.codec.encrypt(lesson.meeting_url),
                    lesson.status,
                    lesson.rate_cents,
                    lesson.lesson_id,
                    now,
                    now,
                ),
            )
            row = db.execute(
                "SELECT id FROM crm_lesson_occurrences WHERE rule_id=? AND original_date=?",
                (lesson.rule_id, original_date.isoformat()),
            ).fetchone()
        return int(row["id"])

    def update_occurrence(
        self,
        occurrence_id: int,
        *,
        status: str | None = None,
        lesson_id: str | None = None,
    ) -> None:
        assignments = ["updated_at=?"]
        values: list[object] = [self._now()]
        if status is not None:
            assignments.append("status=?")
            values.append(status)
        if lesson_id is not None:
            assignments.append("lesson_id=?")
            values.append(lesson_id)
        values.append(occurrence_id)
        with self.connect() as db:
            db.execute(
                f"UPDATE crm_lesson_occurrences SET {', '.join(assignments)} WHERE id=?",  # noqa: S608
                values,
            )

    def _occurrence_from_row(self, row: sqlite3.Row) -> ScheduledLesson:
        return ScheduledLesson(
            occurrence_id=row["id"],
            rule_id=row["rule_id"],
            original_date=date.fromisoformat(row["original_date"])
            if row["original_date"]
            else None,
            student_id=row["student_id"],
            student_name=row["student_name"],
            starts_at=datetime.fromisoformat(row["starts_at"]),
            duration_minutes=row["duration_minutes"],
            subject=row["subject"],
            topic=row["topic"],
            meeting_url=self.codec.decrypt(row["meeting_secret"]) or "",
            status=row["status"],
            rate_cents=row["rate_cents"],
            lesson_id=row["lesson_id"],
        )

    def lessons_for_week(self, week_start: date) -> list[ScheduledLesson]:
        week_end = week_start + timedelta(days=7)
        with self.connect() as db:
            occurrence_rows = db.execute(
                """
                SELECT o.*, s.full_name AS student_name
                FROM crm_lesson_occurrences o
                JOIN crm_students s ON s.id=o.student_id
                WHERE o.starts_at>=? AND o.starts_at<?
                """,
                (
                    datetime.combine(week_start, time()).isoformat(),
                    datetime.combine(week_end, time()).isoformat(),
                ),
            ).fetchall()
            rule_rows = db.execute(
                """
                SELECT r.*, s.full_name AS student_name
                FROM crm_schedule_rules r
                JOIN crm_students s ON s.id=r.student_id
                WHERE r.active=1
                ORDER BY r.weekday, r.start_minute
                """
            ).fetchall()
        occurrences = [self._occurrence_from_row(row) for row in occurrence_rows]
        exception_keys = {
            (item.rule_id, item.original_date)
            for item in occurrences
            if item.rule_id is not None and item.original_date is not None
        }
        for row in rule_rows:
            rule = self._rule_from_row(row)
            lesson_date = week_start + timedelta(days=rule.weekday)
            if lesson_date < rule.valid_from:
                continue
            if rule.valid_until and lesson_date > rule.valid_until:
                continue
            if (rule.id, lesson_date) in exception_keys:
                continue
            starts_at = datetime.combine(
                lesson_date,
                time(hour=rule.start_minute // 60, minute=rule.start_minute % 60),
            )
            occurrences.append(
                ScheduledLesson(
                    rule_id=rule.id,
                    original_date=lesson_date,
                    student_id=rule.student_id,
                    student_name=row["student_name"],
                    starts_at=starts_at,
                    duration_minutes=rule.duration_minutes,
                    subject=rule.subject,
                    topic=rule.topic,
                    meeting_url=rule.meeting_url,
                    rate_cents=rule.rate_cents,
                )
            )
        return sorted(occurrences, key=lambda item: item.starts_at)

    def stats(self, week_start: date) -> CrmStats:
        lessons = [item for item in self.lessons_for_week(week_start) if item.status != "cancelled"]
        return CrmStats(
            active_students=len(self.list_students()),
            lessons_this_week=len(lessons),
            planned_revenue_cents=sum(item.rate_cents for item in lessons),
        )
