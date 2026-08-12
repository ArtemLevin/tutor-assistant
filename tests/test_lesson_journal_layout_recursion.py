from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from tutor_assistant.ui.lesson_journal_closeout_stable import LessonJournalCloseoutStablePage
from tutor_assistant.ui.lesson_journal_responsive import _make_detail_card_scrollable


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


def test_splitter_detail_layout_is_resolved_from_detail_pane(
    application: QApplication,
) -> None:
    root = QWidget()
    root_layout = QVBoxLayout(root)
    splitter = QSplitter(Qt.Orientation.Horizontal)
    root_layout.addWidget(splitter)
    splitter.addWidget(QWidget())

    details = QFrame()
    details_layout = QVBoxLayout(details)
    due_layout = QHBoxLayout()
    due_enabled = QLabel("Дедлайн")
    due_at = QLabel("12.08.2026 18:00")
    due_layout.addWidget(due_enabled)
    due_layout.addWidget(due_at)
    details_layout.addLayout(due_layout)
    splitter.addWidget(details)

    # QSplitter does not expose its pane contents through the outer page layout.
    assert (
        LessonJournalCloseoutStablePage._layout_containing_widget(
            root_layout,
            due_enabled,
        )
        is None
    )
    found = LessonJournalCloseoutStablePage._layout_containing_widget(
        details_layout,
        due_enabled,
    )
    assert found is due_layout

    root.close()
    application.processEvents()


def test_detail_card_overflow_uses_vertical_scroll_without_field_overlap(
    application: QApplication,
) -> None:
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.addWidget(QWidget())

    details = QFrame()
    details_layout = QVBoxLayout(details)
    for index in range(5):
        field = QLabel(f"Поле {index}")
        field.setMinimumHeight(54)
        details_layout.addWidget(field)

    due_layout = QHBoxLayout()
    due_enabled = QLabel("Дедлайн")
    due_enabled.setMinimumHeight(40)
    due_at = QLabel("12.08.2026 18:00")
    due_at.setMinimumHeight(40)
    due_layout.addWidget(due_enabled)
    due_layout.addWidget(due_at)
    details_layout.addLayout(due_layout)

    for index in range(5, 9):
        field = QLabel(f"Поле {index}")
        field.setMinimumHeight(54)
        details_layout.addWidget(field)

    splitter.addWidget(details)
    splitter.setSizes([420, 280])
    scroll = _make_detail_card_scrollable(
        details,
        splitter,
        1,
        due_layout,
        min_width=220,
        max_width=320,
    )

    splitter.resize(760, 260)
    splitter.show()
    application.processEvents()

    assert scroll.widget() is details
    assert due_layout.direction() == QBoxLayout.Direction.TopToBottom
    assert scroll.verticalScrollBar().maximum() > 0
    assert scroll.horizontalScrollBar().maximum() == 0
    assert details.height() >= details.minimumSizeHint().height()
    assert due_enabled.geometry().bottom() < due_at.geometry().top()

    splitter.close()
    application.processEvents()
