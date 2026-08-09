from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime

from ..lesson_closeout import AttendanceStatus, LessonCloseoutMeta, LessonCloseoutService
from ..lesson_journal import (
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


class CloseoutAwareLessonJournalService(ReversibleLessonJournalService):
    """Journal query with pedagogical closeout metadata and filters."""

    def __init__(self, crm_store, lesson_store=None) -> None:
        super().__init__(crm_store, lesson_store)
        self.closeout_service = LessonCloseoutService(crm_store)

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

    @staticmethod
    def _base_filter(filters: LessonJournalFilter, *, offset: int) -> LessonJournalFilter:
        values = {
            field.name: getattr(filters, field.name)
            for field in fields(LessonJournalFilter)
        }
        values["limit"] = 500
        values["offset"] = offset
        return LessonJournalFilter(**values)

    def _all_base_rows(
        self,
        filters: LessonJournalFilter,
        *,
        now: datetime,
    ) -> list[LessonJournalRow]:
        rows: list[LessonJournalRow] = []
        offset = 0
        while True:
            result = super().search(self._base_filter(filters, offset=offset), now=now)
            rows.extend(result.rows)
            if not result.has_more or not result.rows:
                return rows
            offset += len(result.rows)

    def search(
        self,
        filters: LessonJournalFilter,
        *,
        now: datetime | None = None,
    ) -> LessonJournalResult:
        current_time = now or datetime.now()
        attendance = str(getattr(filters, "attendance", "all") or "all")
        unfinished_only = bool(getattr(filters, "unfinished_only", False))
        base_rows = self._all_base_rows(filters, now=current_time)
        closeouts = self.closeout_service.list_for_occurrences(
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
