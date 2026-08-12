from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QTimer
from PySide6.QtWidgets import QCheckBox, QMessageBox

from ..lesson_journal import HomeworkStatus
from .journal_interactions import ReversibleLessonJournalService


class ScheduleHomeworkReceivedController(QObject):
    """Add a received-homework control to occupied schedule slots."""

    def __init__(
        self,
        page,
        *,
        changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(page)
        self.page = page
        self.grid = page.grid
        self.viewport = self.grid.viewport()
        self.service = ReversibleLessonJournalService(page.store)
        self.changed = changed
        self._controls: dict[tuple[int, int, int], QCheckBox] = {}
        self._sync_pending = False
        self._active = True

        # Keep the deferred sync owned by this controller. A static
        # QTimer.singleShot callable can outlive the QTableWidget during Qt child
        # destruction and invoke Python with an already-deleted C++ object.
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(0)
        self._sync_timer.timeout.connect(self.sync)

        self.page.installEventFilter(self)
        self.viewport.installEventFilter(self)
        self.grid.verticalScrollBar().valueChanged.connect(self.schedule_sync)
        self.grid.horizontalScrollBar().valueChanged.connect(self.schedule_sync)
        self.grid.horizontalHeader().sectionResized.connect(self.schedule_sync)
        self.grid.verticalHeader().sectionResized.connect(self.schedule_sync)
        model = self.grid.model()
        model.modelReset.connect(self.schedule_sync)
        model.layoutChanged.connect(self.schedule_sync)
        model.dataChanged.connect(self.schedule_sync)

        # Qt may destroy the schedule grid before the page/controller itself.
        # Mark the controller inactive from destruction signals without touching
        # any C++ widget in the teardown callback.
        self.page.destroyed.connect(self._deactivate)
        self.grid.destroyed.connect(self._deactivate)
        self.viewport.destroyed.connect(self._deactivate)
        self.schedule_sync()

    def _deactivate(self, *_args) -> None:
        self._active = False
        self._sync_pending = False
        self._controls.clear()

    def eventFilter(self, watched, event) -> bool:
        if not self._active:
            return False
        if (watched is self.page or watched is self.viewport) and event.type() in {
            QEvent.Type.Show,
            QEvent.Type.Resize,
        }:
            self.schedule_sync()
        return False

    def schedule_sync(self, *_args) -> None:
        if not self._active or self._sync_pending:
            return
        self._sync_pending = True
        self._sync_timer.start()

    def _lesson_controls(self) -> dict[tuple[int, int, int], tuple[int, int, object]]:
        desired: dict[tuple[int, int, int], tuple[int, int, object]] = {}
        seen: set[int] = set()
        for lesson in self.page.cell_lessons.values():
            marker = id(lesson)
            if marker in seen:
                continue
            seen.add(marker)
            row = self.page._row_for_time(lesson.starts_at.hour, lesson.starts_at.minute)
            column = lesson.starts_at.weekday()
            if not (
                0 <= row < self.grid.rowCount()
                and 0 <= column < self.grid.columnCount()
            ):
                continue
            if self.page.cell_lessons.get((row, column)) is not lesson:
                continue
            desired[(row, column, marker)] = (row, column, lesson)
        return desired

    def sync(self) -> None:
        self._sync_pending = False
        if not self._active:
            return
        desired = self._lesson_controls()
        for key in tuple(self._controls):
            if key in desired:
                continue
            control = self._controls.pop(key)
            control.hide()
            control.deleteLater()

        for key, (row, column, lesson) in desired.items():
            control = self._controls.get(key)
            if control is None:
                control = self._create_control(row, column, lesson)
                self._controls[key] = control
            self._sync_control(control, lesson)
            self._position_control(control, row, column)

    def _create_control(self, row: int, column: int, lesson) -> QCheckBox:
        control = QCheckBox("ДЗ", self.viewport)
        control.setObjectName("scheduleHomeworkReceived")
        control.setAccessibleName(
            f"ДЗ получено: {lesson.student_name}, {lesson.starts_at:%d.%m.%Y %H:%M}"
        )
        control.setStyleSheet(
            "QCheckBox#scheduleHomeworkReceived {"
            " background: rgba(255, 255, 255, 220);"
            " border-radius: 4px;"
            " padding: 1px 4px;"
            " }"
        )
        control.pressed.connect(lambda: self.grid.setCurrentCell(row, column))
        control.toggled.connect(
            lambda checked, current=lesson, checkbox=control: self._changed(
                current,
                checkbox,
                checked,
            )
        )
        control.show()
        return control

    def _sync_control(self, control: QCheckBox, lesson) -> None:
        snapshot = self.service.snapshot_homework(lesson)
        received = bool(snapshot.received_at is not None)
        blocker = QSignalBlocker(control)
        control.setChecked(received)
        control.setEnabled(lesson.status != "cancelled")
        del blocker
        state = "получено" if received else "не получено"
        control.setToolTip(
            f"ДЗ {state}. Изменить отметку о получении домашней работы для "
            f"{lesson.student_name}."
        )
        control.setAccessibleDescription(
            f"Домашняя работа сейчас: {state}. Галочка синхронизирована с Журналом занятий."
        )

    def _position_control(self, control: QCheckBox, row: int, column: int) -> None:
        x = self.grid.columnViewportPosition(column)
        y = self.grid.rowViewportPosition(row)
        width = self.grid.columnWidth(column)
        span = max(1, self.grid.rowSpan(row, column))
        height = sum(
            self.grid.rowHeight(current)
            for current in range(row, min(self.grid.rowCount(), row + span))
        )
        viewport_rect = self.viewport.rect()
        visible = (
            width > 0
            and height > 0
            and x < viewport_rect.right()
            and x + width > viewport_rect.left()
            and y < viewport_rect.bottom()
            and y + height > viewport_rect.top()
        )
        control.setVisible(visible)
        if not visible:
            return

        # Use the full wording when the column is wide enough; compact schedule
        # columns keep a short label while retaining the full tooltip/accessibility text.
        control.setText("ДЗ получено" if width >= 190 else "ДЗ")
        hint = control.sizeHint()
        control_width = min(max(42, hint.width()), max(42, width - 8))
        control_height = min(hint.height(), max(20, height - 4))
        control.setGeometry(
            x + max(4, width - control_width - 4),
            y + max(2, height - control_height - 3),
            control_width,
            control_height,
        )
        control.raise_()

    def _changed(self, lesson, control: QCheckBox, received: bool) -> None:
        if not self._active:
            return
        snapshot = self.service.snapshot_homework(lesson)
        previous = bool(snapshot.received_at is not None)
        if previous == received:
            return
        target = HomeworkStatus.RECEIVED if received else HomeworkStatus.SENT
        try:
            occurrence_id = self.service.set_homework_status(lesson, target)
        except Exception as exc:
            blocker = QSignalBlocker(control)
            control.setChecked(previous)
            del blocker
            QMessageBox.critical(
                self.page,
                "Домашняя работа",
                f"Не удалось сохранить отметку о получении ДЗ: {exc}",
            )
            return

        lesson.occurrence_id = occurrence_id
        self.page.refresh()
        if self.changed is not None:
            self.changed()
        self.schedule_sync()

    def checkbox_for(self, row: int, column: int) -> QCheckBox | None:
        if not self._active:
            return None
        for (control_row, control_column, _marker), control in self._controls.items():
            if control_row == row and control_column == column:
                return control
        return None
