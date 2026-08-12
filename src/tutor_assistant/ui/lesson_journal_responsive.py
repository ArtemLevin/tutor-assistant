from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QWidget,
)

from .lesson_journal_closeout_stable import LessonJournalCloseoutStablePage


def _make_detail_card_scrollable(
    details: QWidget,
    splitter: QSplitter,
    index: int,
    due_layout: QBoxLayout | None,
    *,
    min_width: int,
    max_width: int,
) -> QScrollArea:
    """Move a detail card into a vertical scroll area without changing its controls."""
    details_layout = details.layout()
    if details_layout is None:
        raise RuntimeError("Layout карточки выбранного занятия недоступен")
    details_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

    if due_layout is not None:
        due_layout.setDirection(QBoxLayout.Direction.TopToBottom)
        due_layout.setSpacing(6)

    scroll = QScrollArea()
    scroll.setObjectName("lessonJournalDetailsScroll")
    scroll.setAccessibleName("Карточка выбранного занятия")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setMinimumWidth(min_width)
    scroll.setMaximumWidth(max_width)
    scroll.setSizePolicy(
        QSizePolicy.Policy.Preferred,
        QSizePolicy.Policy.Expanding,
    )

    # Width constraints belong to the splitter pane, not to the scrollable
    # content widget. Keeping them on the content prevents QScrollArea from
    # resizing it correctly at different Windows scale factors.
    details.setMinimumWidth(0)
    details.setMaximumWidth(16_777_215)
    details.setSizePolicy(
        QSizePolicy.Policy.Expanding,
        QSizePolicy.Policy.MinimumExpanding,
    )

    sizes = splitter.sizes()
    splitter.replaceWidget(index, scroll)
    if splitter.indexOf(scroll) != index:
        raise RuntimeError("Не удалось заменить карточку на прокручиваемую область")
    scroll.setWidget(details)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(index, 0)
    if len(sizes) == splitter.count():
        splitter.setSizes(sizes)
    return scroll


class LessonJournalResponsivePage(LessonJournalCloseoutStablePage):
    """Production journal page with a resilient, scrollable lesson detail card."""

    detail_pane_min_width = 340
    detail_pane_max_width = 480

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._install_responsive_detail_card()

    def _install_responsive_detail_card(self) -> None:
        details = self.detail_payment.parentWidget()
        if details is None:
            raise RuntimeError("Карточка выбранного занятия недоступна")
        splitter = details.parentWidget()
        if not isinstance(splitter, QSplitter):
            raise RuntimeError("Не найден splitter карточки выбранного занятия")
        index = splitter.indexOf(details)
        if index < 0:
            raise RuntimeError("Карточка выбранного занятия отсутствует в splitter")

        # QSplitter owns its child panes internally rather than exposing them
        # through the page layout hierarchy. Search for the deadline row from
        # the detail card's own layout instead of walking from self.layout().
        details_layout = details.layout()
        if details_layout is None:
            raise RuntimeError("Layout карточки выбранного занятия недоступен")
        due_layout = self._layout_containing_widget(details_layout, self.due_enabled)

        # The closeout layer adds enough controls that the card can no longer fit
        # vertically into a compact main window. Keep the layout's real minimum
        # height and move overflow into scrolling instead of compressing controls.
        scroll = _make_detail_card_scrollable(
            details,
            splitter,
            index,
            due_layout if isinstance(due_layout, QBoxLayout) else None,
            min_width=self.detail_pane_min_width,
            max_width=self.detail_pane_max_width,
        )

        self.detail_panel = details
        self.detail_scroll_area = scroll
