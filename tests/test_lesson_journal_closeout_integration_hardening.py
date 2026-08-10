from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication, QWidget

from tutor_assistant.ui.lesson_journal_integration import LessonJournalCloseGuard


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


class FakeCloseoutPage:
    def __init__(self) -> None:
        self.allow_exit = False
        self.calls = 0

    def confirm_closeout_before_exit(self) -> bool:
        self.calls += 1
        return self.allow_exit


def test_application_close_guard_can_cancel_and_then_allow_exit(
    application: QApplication,
) -> None:
    window = QWidget()
    page = FakeCloseoutPage()
    guard = LessonJournalCloseGuard(window, page)  # type: ignore[arg-type]
    window.installEventFilter(guard)
    window.show()
    application.processEvents()

    assert window.close() is False
    assert window.isVisible()
    assert page.calls == 1

    page.allow_exit = True
    assert window.close() is True
    assert page.calls == 2
