from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication

from tutor_assistant.crm import CrmStore
from tutor_assistant.ui.lesson_journal_closeout_stable import LessonJournalCloseoutStablePage


class TestCodec:
    def encrypt(self, value: str | None) -> str | None:
        return value

    def decrypt(self, value: str | None) -> str | None:
        return value


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_closeout_page_builds_with_nested_filter_panel(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "journal-startup.sqlite3", TestCodec())

    page = LessonJournalCloseoutStablePage(store)

    assert page.attendance_filter is not None
    assert page.summary_unfinished is not None
    assert page.unfinished_button is not None
    page.close()
    application.processEvents()
