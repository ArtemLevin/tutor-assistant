from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QSplitter,
)

from .lesson_journal_closeout_stable import LessonJournalCloseoutStablePage


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

        # The closeout layer adds enough controls that the card can no longer fit
        # vertically into a compact main window.  Let the layout keep its true
        # minimum height and move the overflow into a vertical scroll area instead
        # of compressing child widgets until they visually collide.
        details_layout = details.layout()
        if details_layout is None:
            raise RuntimeError("Layout карточки выбранного занятия недоступен")
        details_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        # The deadline row is the only dense horizontal field group in the card.
        # Stack it so Windows font/DPI scaling cannot force the checkbox and date
        # editor into the same narrow horizontal space.
        due_layout = self._required_layout_for(self.due_enabled, "срока домашней работы")
        if isinstance(due_layout, QBoxLayout):
            due_layout.setDirection(QBoxLayout.Direction.TopToBottom)
            due_layout.setSpacing(6)

        scroll = QScrollArea()
        scroll.setObjectName("lessonJournalDetailsScroll")
        scroll.setAccessibleName("Карточка выбранного занятия")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setMinimumWidth(self.detail_pane_min_width)
        scroll.setMaximumWidth(self.detail_pane_max_width)
        scroll.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding,
        )

        # Width constraints belong to the splitter pane, not to the scrollable
        # content widget.  Keeping them on the content prevents QScrollArea from
        # resizing it correctly at different scale factors.
        details.setMinimumWidth(0)
        details.setMaximumWidth(16_777_215)
        details.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.MinimumExpanding,
        )

        sizes = splitter.sizes()
        replaced = splitter.replaceWidget(index, scroll)
        if replaced is not details:
            raise RuntimeError("Не удалось заменить карточку на прокручиваемую область")
        scroll.setWidget(details)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(index, 0)
        if len(sizes) == splitter.count():
            splitter.setSizes(sizes)

        self.detail_panel = details
        self.detail_scroll_area = scroll
