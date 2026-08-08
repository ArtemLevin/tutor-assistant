from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from .crm import CrmStore, ScheduledLesson


class HomeworkStatus(StrEnum):
    NONE = "none"
    ASSIGNED = "assigned"
    SENT = "sent"
    RECEIVED = "received"
    CHECKED = "checked"
    RETURNED = "returned"


HOMEWORK_STAGE_COLUMNS: tuple[tuple[HomeworkStatus, str], ...] = (
    (HomeworkStatus.ASSIGNED, "assigned_at"),
    (HomeworkStatus.SENT, "sent_at"),
    (HomeworkStatus.RECEIVED, "received_at"),
    (HomeworkStatus.CHECKED, "checked_at"),
    (HomeworkStatus.RETURNED, "returned_at"),
)
HOMEWORK_STAGE_RANK = {
    HomeworkStatus.NONE: 0,
    HomeworkStatus.ASSIGNED: 1,
    HomeworkStatus.SENT: 2,
    HomeworkStatus.RECEIVED: 3,
    HomeworkStatus.CHECKED: 4,
    HomeworkStatus.RETURNED: 5,
}
SUBJECT_SEARCH_LABELS = {
    "mathematics": "математика",
    "physics": "физика",
    "chemistry": "химия",
}


class LessonStoreLike(Protocol):
    def list(self, *, limit: int = 1000): ...

    def get(self, lesson_id: str): ...


@dataclass(frozen=True, slots=True)
class HomeworkMeta:
    occurrence_id: int
    assigned_at: datetime | None = None
    sent_at: datetime | None = None
    due_at: datetime | None = None
    received_at: datetime | None = None
    checked_at: datetime | None = None
    returned_at: datetime | None = None

    @property
    def status(self) -> HomeworkStatus:
        if self.returned_at is not None:
            return HomeworkStatus.RETURNED
        if self.checked_at is not None:
            return HomeworkStatus.CHECKED
        if self.received_at is not None:
            return HomeworkStatus.RECEIVED
        if self.sent_at is not None:
            return HomeworkStatus.SENT
        if self.assigned_at is not None:
            return HomeworkStatus.ASSIGNED
        return HomeworkStatus.NONE


@dataclass(slots=True)
class LessonJournalFilter:
    query: str = ""
    student_id: str | None = None
    subject: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    payment: str = "all"
    lesson_status: str | None = None
    homework: str = "all"
    processing_status: str | None = None
    recording: bool | None = None
    transcript: bool | None = None
    materials: bool | None = None
    attention_only: bool = False
    time_from_minute: int | None = None
    time_to_minute: int | None = None
    sort: str = "date_desc"
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class LessonJournalRow:
    lesson: ScheduledLesson
    homework: HomeworkMeta | None
    processing_status: str | None
    recording_exists: bool
    transcript_exists: bool
    materials_exist: bool
    requires_attention: bool

    @property
    def homework_status(self) -> HomeworkStatus:
        return self.homework.status if self.homework is not None else HomeworkStatus.NONE

    @property
    def homework_due_at(self) -> datetime | None:
        return self.homework.due_at if self.homework is not None else None


@dataclass(frozen=True, slots=True)
class LessonJournalSummary:
    lessons: int = 0
    students: int = 0
    planned_cents: int = 0
    paid_cents: int = 0
    unpaid_cents: int = 0
    homework_waiting: int = 0
    homework_review: int = 0
    attention: int = 0


@dataclass(frozen=True, slots=True)
class LessonJournalResult:
    rows: tuple[LessonJournalRow, ...] = field(default_factory=tuple)
    summary: LessonJournalSummary = field(default_factory=LessonJournalSummary)
    total: int = 0
    has_more: bool = False


class LessonJournalService:
    """Local administrative journal over schedule occurrences and lesson metadata."""

    max_range_days = 1095

    def __init__(
        self,
        crm_store: CrmStore,
        lesson_store: LessonStoreLike | None = None,
    ) -> None:
        self.crm_store = crm_store
        self.lesson_store = lesson_store
        self._initialize()

    def _initialize(self) -> None:
        with self.crm_store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS crm_lesson_homework (
                    occurrence_id INTEGER PRIMARY KEY,
                    assigned_at TEXT,
                    sent_at TEXT,
                    due_at TEXT,
                    received_at TEXT,
                    checked_at TEXT,
                    returned_at TEXT,
                    FOREIGN KEY(occurrence_id)
                        REFERENCES crm_lesson_occurrences(id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS crm_homework_due
                    ON crm_lesson_homework(due_at);
                CREATE INDEX IF NOT EXISTS crm_homework_received
                    ON crm_lesson_homework(received_at);
                CREATE INDEX IF NOT EXISTS crm_homework_checked
                    ON crm_lesson_homework(checked_at);
                CREATE INDEX IF NOT EXISTS crm_occurrences_student_start
                    ON crm_lesson_occurrences(student_id, starts_at);
                CREATE INDEX IF NOT EXISTS crm_occurrences_paid_start
                    ON crm_lesson_occurrences(paid, starts_at);
                CREATE INDEX IF NOT EXISTS crm_occurrences_status_start
                    ON crm_lesson_occurrences(status, starts_at);
                """
            )

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        text = str(value).strip()
        return datetime.fromisoformat(text) if text else None

    def _homework_for_occurrences(self, occurrence_ids: set[int]) -> dict[int, HomeworkMeta]:
        if not occurrence_ids:
            return {}
        placeholders = ",".join("?" for _ in occurrence_ids)
        with self.crm_store.connect() as db:
            rows = db.execute(
                f"""
                SELECT occurrence_id, assigned_at, sent_at, due_at,
                       received_at, checked_at, returned_at
                FROM crm_lesson_homework
                WHERE occurrence_id IN ({placeholders})
                """,  # noqa: S608
                tuple(sorted(occurrence_ids)),
            ).fetchall()
        return {
            int(row["occurrence_id"]): HomeworkMeta(
                occurrence_id=int(row["occurrence_id"]),
                assigned_at=self._parse_datetime(row["assigned_at"]),
                sent_at=self._parse_datetime(row["sent_at"]),
                due_at=self._parse_datetime(row["due_at"]),
                received_at=self._parse_datetime(row["received_at"]),
                checked_at=self._parse_datetime(row["checked_at"]),
                returned_at=self._parse_datetime(row["returned_at"]),
            )
            for row in rows
        }

    def _stored_lessons(self) -> dict[str, object]:
        if self.lesson_store is None:
            return {}
        list_lessons = getattr(self.lesson_store, "list", None)
        if not callable(list_lessons):
            return {}
        try:
            try:
                lessons = list(list_lessons(limit=5000))
            except TypeError:
                lessons = list(list_lessons())
        except Exception:
            return {}
        return {
            str(getattr(lesson, "lesson_id")): lesson
            for lesson in lessons
            if getattr(lesson, "lesson_id", None)
        }

    @staticmethod
    def _artifact_presence(stored: object | None) -> tuple[bool, bool, bool, str | None]:
        if stored is None:
            return False, False, False, None
        recording = bool(getattr(stored, "source_audio_local", None))
        artifacts = getattr(stored, "artifacts", None)
        transcript = bool(getattr(artifacts, "verified_transcript", None))
        artifact_values: list[object] = []
        if artifacts is not None:
            model_dump = getattr(artifacts, "model_dump", None)
            if callable(model_dump):
                try:
                    artifact_values.extend(model_dump().values())
                except Exception:
                    pass
            elif hasattr(artifacts, "__dict__"):
                artifact_values.extend(vars(artifacts).values())
        publication = getattr(stored, "publication", None)
        materials = (
            recording
            or transcript
            or any(bool(value) for value in artifact_values)
            or bool(publication)
        )
        status = getattr(stored, "status", None)
        status_value = getattr(status, "value", status)
        return recording, transcript, materials, str(status_value) if status_value else None

    def _lessons_for_range(self, date_from: date, date_to: date) -> list[ScheduledLesson]:
        if date_to < date_from:
            return []
        if (date_to - date_from).days > self.max_range_days:
            raise ValueError("Диапазон журнала ограничен тремя годами")
        cursor = date_from - timedelta(days=date_from.weekday())
        lessons: list[ScheduledLesson] = []
        while cursor <= date_to:
            lessons.extend(self.crm_store.lessons_for_week(cursor))
            cursor += timedelta(days=7)
        result: dict[tuple[object, ...], ScheduledLesson] = {}
        for lesson in lessons:
            if not (date_from <= lesson.starts_at.date() <= date_to):
                continue
            key = (
                lesson.occurrence_id,
                lesson.rule_id,
                lesson.original_date,
                lesson.student_id,
                lesson.starts_at,
            )
            result[key] = lesson
        return list(result.values())

    @staticmethod
    def _is_homework_overdue(meta: HomeworkMeta | None, now: datetime) -> bool:
        if meta is None or meta.due_at is None:
            return False
        return (
            meta.due_at < now
            and HOMEWORK_STAGE_RANK[meta.status]
            < HOMEWORK_STAGE_RANK[HomeworkStatus.RECEIVED]
        )

    @classmethod
    def _requires_attention(
        cls,
        lesson: ScheduledLesson,
        meta: HomeworkMeta | None,
        processing_status: str | None,
        now: datetime,
    ) -> bool:
        past = lesson.ends_at < now
        unpaid = past and lesson.status != "cancelled" and not lesson.paid
        stale_status = past and lesson.status == "planned"
        homework_review = bool(meta and meta.received_at and not meta.checked_at)
        homework_overdue = cls._is_homework_overdue(meta, now)
        processing_problem = processing_status in {"failed", "compile_failed"}
        return unpaid or stale_status or homework_review or homework_overdue or processing_problem

    @staticmethod
    def _time_matches(lesson: ScheduledLesson, filters: LessonJournalFilter) -> bool:
        if filters.time_from_minute is None or filters.time_to_minute is None:
            return True
        minute = lesson.starts_at.hour * 60 + lesson.starts_at.minute
        if filters.time_from_minute <= filters.time_to_minute:
            return filters.time_from_minute <= minute <= filters.time_to_minute
        return minute >= filters.time_from_minute or minute <= filters.time_to_minute

    @classmethod
    def _homework_matches(
        cls,
        row: LessonJournalRow,
        homework_filter: str,
        now: datetime,
    ) -> bool:
        if homework_filter == "all":
            return True
        if homework_filter == "review":
            return bool(row.homework and row.homework.received_at and not row.homework.checked_at)
        if homework_filter == "overdue":
            return cls._is_homework_overdue(row.homework, now)
        try:
            return row.homework_status == HomeworkStatus(homework_filter)
        except ValueError:
            return True

    @staticmethod
    def _payment_matches(
        lesson: ScheduledLesson,
        payment_filter: str,
        now: datetime,
    ) -> bool:
        if payment_filter == "paid":
            return lesson.paid
        if payment_filter == "unpaid":
            return not lesson.paid and lesson.status != "cancelled"
        if payment_filter == "unpaid_past":
            return not lesson.paid and lesson.status != "cancelled" and lesson.ends_at < now
        return True

    @staticmethod
    def _sort_rows(rows: list[LessonJournalRow], sort: str) -> None:
        if sort == "date_asc":
            rows.sort(key=lambda row: row.lesson.starts_at)
        elif sort == "student":
            rows.sort(
                key=lambda row: (
                    row.lesson.student_name.casefold(),
                    -row.lesson.starts_at.timestamp(),
                )
            )
        elif sort == "subject":
            rows.sort(
                key=lambda row: (
                    row.lesson.subject.casefold(),
                    -row.lesson.starts_at.timestamp(),
                )
            )
        elif sort == "payment":
            rows.sort(key=lambda row: (row.lesson.paid, -row.lesson.starts_at.timestamp()))
        elif sort == "homework":
            rows.sort(
                key=lambda row: (
                    HOMEWORK_STAGE_RANK[row.homework_status],
                    -row.lesson.starts_at.timestamp(),
                )
            )
        else:
            rows.sort(key=lambda row: row.lesson.starts_at, reverse=True)

    @staticmethod
    def _summary(rows: list[LessonJournalRow], now: datetime) -> LessonJournalSummary:
        active = [row for row in rows if row.lesson.status != "cancelled"]
        planned = sum(row.lesson.rate_cents for row in active)
        paid = sum(row.lesson.rate_cents for row in active if row.lesson.paid)
        unpaid = sum(
            row.lesson.rate_cents
            for row in active
            if not row.lesson.paid and row.lesson.ends_at < now
        )
        waiting = sum(
            bool(row.homework and row.homework.sent_at and not row.homework.received_at)
            for row in active
        )
        review = sum(
            bool(row.homework and row.homework.received_at and not row.homework.checked_at)
            for row in active
        )
        return LessonJournalSummary(
            lessons=len(rows),
            students=len({row.lesson.student_id for row in rows}),
            planned_cents=planned,
            paid_cents=paid,
            unpaid_cents=unpaid,
            homework_waiting=waiting,
            homework_review=review,
            attention=sum(row.requires_attention for row in rows),
        )

    def search(
        self,
        filters: LessonJournalFilter,
        *,
        now: datetime | None = None,
    ) -> LessonJournalResult:
        current_time = now or datetime.now()
        date_from = filters.date_from or (current_time.date() - timedelta(days=90))
        date_to = filters.date_to or (current_time.date() + timedelta(days=30))
        lessons = self._lessons_for_range(date_from, date_to)
        homework = self._homework_for_occurrences(
            {lesson.occurrence_id for lesson in lessons if lesson.occurrence_id is not None}
        )
        stored_lessons = self._stored_lessons()
        query = filters.query.strip().casefold()
        rows: list[LessonJournalRow] = []

        for lesson in lessons:
            meta = homework.get(lesson.occurrence_id) if lesson.occurrence_id is not None else None
            stored = stored_lessons.get(lesson.lesson_id or "")
            recording, transcript, materials, processing_status = self._artifact_presence(stored)
            requires_attention = self._requires_attention(
                lesson,
                meta,
                processing_status,
                current_time,
            )
            row = LessonJournalRow(
                lesson=lesson,
                homework=meta,
                processing_status=processing_status,
                recording_exists=recording,
                transcript_exists=transcript,
                materials_exist=materials,
                requires_attention=requires_attention,
            )
            if filters.student_id and lesson.student_id != filters.student_id:
                continue
            if filters.subject and lesson.subject != filters.subject:
                continue
            if filters.lesson_status and lesson.status != filters.lesson_status:
                continue
            if not self._payment_matches(lesson, filters.payment, current_time):
                continue
            if not self._homework_matches(row, filters.homework, current_time):
                continue
            if filters.processing_status and processing_status != filters.processing_status:
                continue
            if filters.recording is not None and recording != filters.recording:
                continue
            if filters.transcript is not None and transcript != filters.transcript:
                continue
            if filters.materials is not None and materials != filters.materials:
                continue
            if filters.attention_only and not requires_attention:
                continue
            if not self._time_matches(lesson, filters):
                continue
            if query:
                haystack = " ".join(
                    (
                        lesson.student_name,
                        lesson.student_id,
                        lesson.subject,
                        SUBJECT_SEARCH_LABELS.get(lesson.subject.casefold(), ""),
                        lesson.topic,
                        lesson.lesson_id or "",
                        lesson.status,
                        processing_status or "",
                        row.homework_status.value,
                    )
                ).casefold()
                if query not in haystack:
                    continue
            rows.append(row)

        self._sort_rows(rows, filters.sort)
        summary = self._summary(rows, current_time)
        total = len(rows)
        offset = max(0, filters.offset)
        limit = max(1, min(500, filters.limit))
        page = rows[offset : offset + limit]
        return LessonJournalResult(
            rows=tuple(page),
            summary=summary,
            total=total,
            has_more=offset + limit < total,
        )

    def set_paid(self, lesson: ScheduledLesson, paid: bool) -> int:
        return self.crm_store.set_lesson_paid(lesson, paid)

    def set_homework_status(
        self,
        lesson: ScheduledLesson,
        status: HomeworkStatus | str,
        *,
        at: datetime | None = None,
    ) -> int:
        parsed = HomeworkStatus(status)
        occurrence_id = self.crm_store.ensure_occurrence(lesson)
        timestamp = (at or datetime.now()).isoformat(timespec="seconds")
        with self.crm_store.connect() as db:
            existing = db.execute(
                """
                SELECT assigned_at, sent_at, due_at, received_at, checked_at, returned_at
                FROM crm_lesson_homework
                WHERE occurrence_id=?
                """,
                (occurrence_id,),
            ).fetchone()
            values = {
                "assigned_at": existing["assigned_at"] if existing else None,
                "sent_at": existing["sent_at"] if existing else None,
                "due_at": existing["due_at"] if existing else None,
                "received_at": existing["received_at"] if existing else None,
                "checked_at": existing["checked_at"] if existing else None,
                "returned_at": existing["returned_at"] if existing else None,
            }
            if parsed == HomeworkStatus.NONE:
                for _stage, column in HOMEWORK_STAGE_COLUMNS:
                    values[column] = None
                values["due_at"] = None
            else:
                target_rank = HOMEWORK_STAGE_RANK[parsed]
                for stage, column in HOMEWORK_STAGE_COLUMNS:
                    if HOMEWORK_STAGE_RANK[stage] <= target_rank:
                        values[column] = values[column] or timestamp
                    else:
                        values[column] = None
            db.execute(
                """
                INSERT INTO crm_lesson_homework (
                    occurrence_id, assigned_at, sent_at, due_at,
                    received_at, checked_at, returned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(occurrence_id) DO UPDATE SET
                    assigned_at=excluded.assigned_at,
                    sent_at=excluded.sent_at,
                    due_at=excluded.due_at,
                    received_at=excluded.received_at,
                    checked_at=excluded.checked_at,
                    returned_at=excluded.returned_at
                """,
                (
                    occurrence_id,
                    values["assigned_at"],
                    values["sent_at"],
                    values["due_at"],
                    values["received_at"],
                    values["checked_at"],
                    values["returned_at"],
                ),
            )
        return occurrence_id

    def set_homework_due(
        self,
        lesson: ScheduledLesson,
        due_at: datetime | None,
    ) -> int:
        occurrence_id = self.crm_store.ensure_occurrence(lesson)
        value = due_at.isoformat(timespec="seconds") if due_at is not None else None
        with self.crm_store.connect() as db:
            assigned_at = (
                datetime.now().isoformat(timespec="seconds")
                if due_at is not None
                else None
            )
            db.execute(
                """
                INSERT INTO crm_lesson_homework (occurrence_id, assigned_at, due_at)
                VALUES (?, ?, ?)
                ON CONFLICT(occurrence_id) DO UPDATE SET
                    assigned_at=CASE
                        WHEN excluded.due_at IS NOT NULL
                        THEN COALESCE(crm_lesson_homework.assigned_at, excluded.assigned_at)
                        ELSE crm_lesson_homework.assigned_at
                    END,
                    due_at=excluded.due_at
                """,
                (occurrence_id, assigned_at, value),
            )
        return occurrence_id
