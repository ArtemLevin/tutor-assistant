"""PySide6 desktop UI."""

# Keep direct imports of lesson_journal_ux on the stabilized production class.
from . import lesson_journal_ux as _lesson_journal_ux
from .lesson_journal_ux_stable import LessonJournalUXStablePage

_lesson_journal_ux.LessonJournalUXPage = LessonJournalUXStablePage
