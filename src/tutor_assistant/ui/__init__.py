"""PySide6 desktop UI."""

# Keep direct imports of lesson_journal_ux on the stabilized production class.
from . import crm as _crm
from . import lesson_journal_ux as _lesson_journal_ux
from .lesson_journal_ux_stable import LessonJournalUXStablePage
from .schedule_ux_stable import ScheduleDialogStable, SchedulePageStable

_lesson_journal_ux.LessonJournalUXPage = LessonJournalUXStablePage
_crm.ScheduleDialog = ScheduleDialogStable
_crm.SchedulePage = SchedulePageStable
