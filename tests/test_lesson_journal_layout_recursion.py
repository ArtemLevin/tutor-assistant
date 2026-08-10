from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from tutor_assistant.ui.lesson_journal_closeout_stable import LessonJournalCloseoutStablePage


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_layout_search_descends_into_widget_owned_layout(
    application: QApplication,
) -> None:
    root = QWidget()
    root_layout = QVBoxLayout(root)
    panel = QFrame(root)
    panel_layout = QHBoxLayout(panel)
    target = QLabel("Статус", panel)
    panel_layout.addWidget(target)
    root_layout.addWidget(panel)

    found = LessonJournalCloseoutStablePage._layout_containing_widget(
        root_layout,
        target,
    )

    assert found is panel_layout
    root.close()
    application.processEvents()
