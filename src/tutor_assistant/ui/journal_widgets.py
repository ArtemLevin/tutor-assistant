from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from .theme import set_button_kind

STATUS_TEXT_ROLE = int(Qt.ItemDataRole.UserRole) + 40
STATUS_TONE_ROLE = int(Qt.ItemDataRole.UserRole) + 41
ATTENTION_TEXT_ROLE = int(Qt.ItemDataRole.UserRole) + 42
ATTENTION_TONE_ROLE = int(Qt.ItemDataRole.UserRole) + 43


class JournalTone(StrEnum):
    NEUTRAL = "neutral"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class JournalStatusDescriptor:
    text: str
    tone: JournalTone
    accessible_text: str
    attention_text: str = ""
    attention_tone: JournalTone = JournalTone.WARNING


_TONE_COLORS = {
    JournalTone.NEUTRAL: (QColor("#EEF2F6"), QColor("#526174"), QColor("#D7DEE7")),
    JournalTone.INFO: (QColor("#EAF2FF"), QColor("#275AA6"), QColor("#BFD3F4")),
    JournalTone.SUCCESS: (QColor("#E8F7F0"), QColor("#216E50"), QColor("#C6EBD9")),
    JournalTone.WARNING: (QColor("#FFF7E6"), QColor("#8A5A00"), QColor("#F3DDAA")),
    JournalTone.ERROR: (QColor("#FFF0F0"), QColor("#A33636"), QColor("#F3CCCC")),
}


class JournalStatusDelegate(QStyledItemDelegate):
    """Paint compact text chips while preserving native row selection."""

    @staticmethod
    def _tone(value: object) -> JournalTone:
        try:
            return JournalTone(str(value))
        except ValueError:
            return JournalTone.NEUTRAL

    def _draw_chip(
        self,
        painter: QPainter,
        rect: QRect,
        text: str,
        tone: JournalTone,
    ) -> int:
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 18
        chip = QRect(rect.x(), rect.y(), width, min(26, rect.height()))
        background, foreground, border = _TONE_COLORS[tone]
        painter.setBrush(background)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(chip, 8, 8)
        painter.setPen(foreground)
        painter.drawText(
            chip.adjusted(9, 0, -9, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )
        return width

    def paint(self, painter: QPainter, option, index) -> None:
        base = QStyleOptionViewItem(option)
        self.initStyleOption(base, index)
        base.text = ""
        style = base.widget.style() if base.widget is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, base, painter, base.widget)

        text = str(index.data(STATUS_TEXT_ROLE) or index.data(Qt.ItemDataRole.DisplayRole) or "")
        attention = str(index.data(ATTENTION_TEXT_ROLE) or "")
        painter.save()
        content = option.rect.adjusted(6, 10, -6, -10)
        used = self._draw_chip(
            painter,
            content,
            text,
            self._tone(index.data(STATUS_TONE_ROLE)),
        )
        if attention:
            content.translate(used + 6, 0)
            self._draw_chip(
                painter,
                content,
                attention,
                self._tone(index.data(ATTENTION_TONE_ROLE)),
            )
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(max(size.width(), 150), max(size.height(), 46))


class JournalToastBar(QFrame):
    undo_requested = Signal()
    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None, *, timeout_ms: int = 8000) -> None:
        super().__init__(parent)
        self.timeout_ms = timeout_ms
        self._remaining_ms = timeout_ms
        self._paused = False
        self.setObjectName("journalToast")
        self.setAccessibleName("Уведомление журнала")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            """
            QFrame#journalToast {
                background: #172033;
                border: 1px solid #354056;
                border-radius: 10px;
            }
            QLabel#journalToastMessage {
                color: #FFFFFF;
                padding: 2px 4px;
                font-weight: 600;
            }
            """
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(10)
        self.message = QLabel()
        self.message.setObjectName("journalToastMessage")
        self.message.setWordWrap(True)
        layout.addWidget(self.message, 1)
        self.undo_button = set_button_kind(QPushButton("Отменить"), "ghost")
        self.undo_button.setAccessibleName("Отменить последнее изменение журнала")
        self.undo_button.clicked.connect(self.undo_requested)
        layout.addWidget(self.undo_button)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        for widget in (self, self.message, self.undo_button):
            widget.installEventFilter(self)
        self.hide()

    def show_message(
        self,
        text: str,
        *,
        undo_available: bool,
        timeout_ms: int | None = None,
    ) -> None:
        self.message.setText(text)
        self.setAccessibleDescription(text)
        self.undo_button.setVisible(undo_available)
        self._remaining_ms = timeout_ms or self.timeout_ms
        self.timer.start(self._remaining_ms)
        self.show()
        self.raise_()

    def dismiss(self) -> None:
        self.timer.stop()
        if self.isVisible():
            self.hide()
            self.dismissed.emit()

    def pause(self) -> None:
        if not self.timer.isActive():
            return
        self._remaining_ms = max(500, self.timer.remainingTime())
        self.timer.stop()
        self._paused = True

    def resume(self) -> None:
        if not self._paused or not self.isVisible():
            return
        self._paused = False
        self.timer.start(self._remaining_ms)

    def eventFilter(self, watched, event) -> bool:
        if event.type() in {QEvent.Type.Enter, QEvent.Type.FocusIn}:
            self.pause()
        elif event.type() == QEvent.Type.Leave:
            if not self.undo_button.hasFocus():
                self.resume()
        elif event.type() == QEvent.Type.FocusOut:
            self.resume()
        return super().eventFilter(watched, event)


class JournalEmptyState(QWidget):
    action_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("journalEmptyState")
        self.setAccessibleName("Пустое состояние журнала занятий")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 36, 32, 36)
        layout.setSpacing(10)
        layout.addStretch(1)
        self.icon = QLabel("○")
        self.icon.setObjectName("journalEmptyIcon")
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setAccessibleName("Пустой журнал")
        layout.addWidget(self.icon)
        self.title = QLabel()
        self.title.setObjectName("tileTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.description = QLabel()
        self.description.setObjectName("muted")
        self.description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.primary = set_button_kind(QPushButton(), "primary")
        self.primary.clicked.connect(
            lambda: self.action_requested.emit(str(self.primary.property("journalAction") or ""))
        )
        actions.addWidget(self.primary)
        self.secondary = set_button_kind(QPushButton(), "ghost")
        self.secondary.clicked.connect(
            lambda: self.action_requested.emit(str(self.secondary.property("journalAction") or ""))
        )
        actions.addWidget(self.secondary)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(2)

    def configure(
        self,
        *,
        title: str,
        description: str,
        primary_text: str = "",
        primary_action: str = "",
        secondary_text: str = "",
        secondary_action: str = "",
    ) -> None:
        self.title.setText(title)
        self.description.setText(description)
        self.setAccessibleDescription(f"{title}. {description}")
        self.primary.setText(primary_text)
        self.primary.setProperty("journalAction", primary_action)
        self.primary.setVisible(bool(primary_text and primary_action))
        self.primary.setAccessibleName(primary_text or "")
        self.secondary.setText(secondary_text)
        self.secondary.setProperty("journalAction", secondary_action)
        self.secondary.setVisible(bool(secondary_text and secondary_action))
        self.secondary.setAccessibleName(secondary_text or "")
