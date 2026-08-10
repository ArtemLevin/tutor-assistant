from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..lesson_closeout import (
    AttendanceStatus,
    LessonCloseoutMeta,
    LessonCloseoutService,
    LessonCloseoutSnapshot,
)
from ..lesson_journal import (
    SUBJECT_SEARCH_LABELS,
    LessonJournalFilter,
    LessonJournalResult,
    LessonJournalRow,
    LessonJournalSummary,
)
from .journal_interactions import ReversibleLessonJournalService


@dataclass
class CloseoutJournalFilter(LessonJournalFilter):
    attendance: str = "all"
    unfinished_only: bool = False


@dataclass(frozen=True, slots=True)
class CloseoutJournalRow(LessonJournalRow):
    closeout: LessonCloseoutMeta | None = None

    @property
    def attendance(self) -> AttendanceStatus:
        if self.closeout is None:
            return AttendanceStatus.UNKNOWN
        return self.closeout.attendance

    @property
    def is_closed(self) -> bool:
        return bool(self.closeout and self.closeout.closed_at is not None)


@dataclass(frozen=True, slots=True)
class CloseoutJournalSummary(LessonJournalSummary):
    unfinished: int = 0


class ReversibleCloseoutService(LessonCloseoutService):
    """Closeout persistence optimized for journal state and close/reopen Undo."""

    def list_states_for_occurrences(
        self,
        occurrence_ids: set[int],
    ) -> dict[int, LessonCloseoutMeta]:
        """Load table state without decrypting teacher notes for every journal row."""
        if not occurrence_ids:
            return {}
        placeholders = ",".join("?" for _ in occurrence_ids)
        with self.crm_store.connect() as db:
            rows = db.execute(
                f"""
                SELECT occurrence_id, attendance, closed_at, updated_at
                FROM crm_lesson_closeout
                WHERE occurrence_id IN ({placeholders})
                """,  # noqa: S608
                tuple(sorted(occurrence_ids)),
            ).fetchall()
        result: dict[int, LessonCloseoutMeta] = {}
        for row in rows:
            try:
                attendance = AttendanceStatus(str(row["attendance"]))
            except ValueError:
                attendance = AttendanceStatus.UNKNOWN
            occurrence_id = int(row["occurrence_id"])
            result[occurrence_id] = LessonCloseoutMeta(
                occurrence_id=occurrence_id,
                attendance=attendance,
                teacher_note="",
                closed_at=self._parse_datetime(row["closed_at"]),
                updated_at=self._parse_datetime(row["updated_at"]),
            )
        return result

    def reopen_with_current_draft(
        self,
        lesson,
        snapshot: LessonCloseoutSnapshot,
        *,
        attendance: AttendanceStatus | str,
        teacher_note: str,
        at: datetime | None = None,
    ) -> int:
        """Undo closing while retaining the closeout values the teacher just entered."""
        parsed = AttendanceStatus(attendance)
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
            self._write_closeout(
                db,
                occurrence_id=occurrence_id,
                attendance=parsed,
                teacher_note=teacher_note.strip(),
                closed_at=(snapshot.closed_at if snapshot.closeout_existed else None),
                updated_at=now,
            )
        return occurrence_id


class CloseoutAwareLessonJournalService(ReversibleLessonJournalService):
    """Journal query with pedagogical closeout metadata and filters."""

    def __init__(self, crm_store, lesson_store=None) -> None:
        super().__init__(crm_store, lesson_store)
        self.closeout_service = ReversibleCloseoutService(crm_store)

    @staticmethod
    def _unfinished(
        row: CloseoutJournalRow,
        now: datetime,
    ) -> bool:
        lesson = row.lesson
        if lesson.status == "cancelled" or lesson.ends_at >= now:
            return False
        return bool(
            lesson.status != "completed"
            or row.closeout is None
            or row.closeout.closed_at is None
            or row.attendance == AttendanceStatus.UNKNOWN
        )

    @staticmethod
    def _attendance_matches(row: CloseoutJournalRow, value: str) -> bool:
        if value == "all":
            return True
        try:
            return row.attendance == AttendanceStatus(value)
        except ValueError:
            return True

    def search(
        self,
        filters: LessonJournalFilter,
        *,
        now: datetime | None = None,
    ) -> LessonJournalResult:
        """Build the filtered journal once, then enrich it with lightweight closeout state."""
        current_time = now or datetime.now()
        date_from = filters.date_from or (current_time.date() - timedelta(days=90))
        date_to = filters.date_to or (current_time.date() + timedelta(days=30))
        attendance = str(getattr(filters, "attendance", "all") or "all")
        unfinished_only = bool(getattr(filters, "unfinished_only", False))

        lessons = self._lessons_for_range(date_from, date_to)
        homework = self._homework_for_occurrences(
            {lesson.occurrence_id for lesson in lessons if lesson.occurrence_id is not None}
        )
        stored_lessons = self._stored_lessons()
        query = filters.query.strip().casefold()
        base_rows: list[LessonJournalRow] = []

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
            base_rows.append(row)

        closeouts = self.closeout_service.list_states_for_occurrences(
            {
                row.lesson.occurrence_id
                for row in base_rows
                if row.lesson.occurrence_id is not None
            }
        )
        rows = [
            CloseoutJournalRow(
                lesson=row.lesson,
                homework=row.homework,
                processing_status=row.processing_status,
                recording_exists=row.recording_exists,
                transcript_exists=row.transcript_exists,
                materials_exist=row.materials_exist,
                requires_attention=row.requires_attention,
                closeout=(
                    closeouts.get(row.lesson.occurrence_id)
                    if row.lesson.occurrence_id is not None
                    else None
                ),
            )
            for row in base_rows
        ]
        rows = [row for row in rows if self._attendance_matches(row, attendance)]
        if unfinished_only:
            rows = [row for row in rows if self._unfinished(row, current_time)]

        self._sort_rows(rows, filters.sort)
        base_summary = self._summary(rows, current_time)
        summary = CloseoutJournalSummary(
            lessons=base_summary.lessons,
            students=base_summary.students,
            planned_cents=base_summary.planned_cents,
            paid_cents=base_summary.paid_cents,
            unpaid_cents=base_summary.unpaid_cents,
            homework_waiting=base_summary.homework_waiting,
            homework_review=base_summary.homework_review,
            attention=base_summary.attention,
            unfinished=sum(self._unfinished(row, current_time) for row in rows),
        )
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
