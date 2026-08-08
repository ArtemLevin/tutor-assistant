from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from PySide6.QtCore import QSettings, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidgetItem,
    QWidget,
)

from ..lesson_journal import (
    HomeworkStatus,
    LessonJournalFilter,
    LessonJournalResult,
    LessonJournalRow,
)
from .lesson_journal import LESSON_STATUS_LABELS, LessonJournalPage
from .localization import subject_label
from .theme import set_button_kind

COMPACT_HOMEWORK_LABELS = {
    HomeworkStatus.NONE: "—",
    HomeworkStatus.ASSIGNED: "Назначено",
    HomeworkStatus.SENT: "Отправлено",
    HomeworkStatus.RECEIVED: "Получено",
    HomeworkStatus.CHECKED: "Проверено",
    HomeworkStatus.RETURNED: "Готово",
}


class JournalSmartView(StrEnum):
    ALL = "all"
    ATTENTION = "attention"
    UNPAID = "unpaid"
    HOMEWORK_REVIEW = "homework_review"


@dataclass(frozen=True, slots=True)
class JournalViewAnchor:
    row_key: str
    student_id: str
    starts_at: datetime
    row_index: int
    vertical_scroll: int


class LessonJournalUXPage(LessonJournalPage):
    """Focused UX refinement for the operational lesson journal."""

    def __init__(self, *args, **kwargs) -> None:
        raw = QSettings("TutorAssistant", "TutorAssistant").value(
            "ux6/journal/filters",
            "",
        )
        self._pending_ux_state: dict[str, object] | None = None
        if raw:
            try:
                decoded = json.loads(str(raw))
                if isinstance(decoded, dict):
                    self._pending_ux_state = decoded
            except (TypeError, json.JSONDecodeError):
                pass
        self._ux_ready = False
        self.current_view = JournalSmartView.ALL
        self._smart_buttons: dict[JournalSmartView, QPushButton] = {}
        self._chip_buttons: list[QPushButton] = []
        super().__init__(*args, **kwargs)
        self._install_ux()
        self._ux_ready = True
        self.search.clear()
        if self._pending_ux_state is None:
            self.period_filter.setCurrentIndex(
                max(0, self.period_filter.findData("this_month"))
            )
            self._apply_period_preset()
        else:
            self._restore_ux_state(self._pending_ux_state)
        self._update_filter_ui()
        self.refresh()

    def _install_ux(self) -> None:
        self.setStyleSheet(
            self.styleSheet()
            + """
            QPushButton#journalSmartButton {
                min-height: 34px;
                padding: 0 12px;
                color: #526174;
                background: transparent;
                border: 1px solid #D9E0E8;
                border-radius: 9px;
            }
            QPushButton#journalSmartButton:checked {
                color: #275AA6;
                background: #EAF2FF;
                border-color: #BFD3F4;
                font-weight: 700;
            }
            QPushButton#journalFilterChip {
                min-height: 28px;
                max-height: 28px;
                padding: 0 10px;
                color: #344054;
                background: #F1F4F8;
                border: 1px solid #D9E0E8;
                border-radius: 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#journalSummaryPill {
                border-radius: 13px;
                padding: 5px 11px;
                font-size: 12px;
                font-weight: 650;
            }
            QLabel#journalSummaryPill[tone="neutral"] {
                color: #526174;
                background: #EEF2F6;
                border: 1px solid #DCE4ED;
            }
            QLabel#journalSummaryPill[tone="success"] {
                color: #216E50;
                background: #E8F7F0;
                border: 1px solid #C6EBD9;
            }
            QLabel#journalSummaryPill[tone="warning"] {
                color: #8A5A00;
                background: #FFF7E6;
                border: 1px solid #F3DDAA;
            }
            QLabel#journalSummaryPill[tone="error"] {
                color: #A33636;
                background: #FFF0F0;
                border: 1px solid #F3CCCC;
            }
            """
        )

        main_layout = self.layout()
        smart_layout = main_layout.itemAt(1).layout()
        filter_panel = main_layout.itemAt(2).widget()
        filter_layout = filter_panel.layout()
        basic_layout = filter_layout.itemAt(0).layout()
        summary_layout = main_layout.itemAt(3).layout()

        self.smart_group = QButtonGroup(self)
        self.smart_group.setExclusive(True)
        mapping = {
            "all": JournalSmartView.ALL,
            "attention": JournalSmartView.ATTENTION,
            "unpaid": JournalSmartView.UNPAID,
            "homework_review": JournalSmartView.HOMEWORK_REVIEW,
        }
        for index in range(smart_layout.count()):
            button = smart_layout.itemAt(index).widget()
            if not isinstance(button, QPushButton):
                continue
            view = mapping.get(str(button.property("journalView") or ""))
            if view is None:
                continue
            button.setObjectName("journalSmartButton")
            button.setCheckable(True)
            self.smart_group.addButton(button)
            self._smart_buttons[view] = button
        self._set_smart_view(JournalSmartView.ALL)
        self._install_period_presets()

        self.filters_toggle = set_button_kind(QPushButton("Фильтры"), "ghost")
        self.filters_toggle.setCheckable(True)
        self.filters_toggle.setAccessibleName("Показать дополнительные фильтры журнала")
        basic_layout.addWidget(self.filters_toggle)

        self._advanced_widgets = (
            self.payment_filter,
            self.homework_filter,
            self.status_filter,
            self.time_enabled,
            self.time_from,
            self.time_to,
            self.sort_filter,
            self.processing_filter,
            self.recording_filter,
            self.transcript_filter,
            self.materials_filter,
        )
        self.attention_only.setVisible(False)
        self.attention_only.setChecked(False)
        self._set_advanced_visible(False)
        self.filters_toggle.toggled.connect(self._advanced_toggled)

        self.date_from.setVisible(self.period_filter.currentData() == "custom")
        self.date_to.setVisible(self.period_filter.currentData() == "custom")

        self.chips_widget = QWidget()
        self.chips_layout = QHBoxLayout(self.chips_widget)
        self.chips_layout.setContentsMargins(0, 0, 0, 0)
        self.chips_layout.setSpacing(6)
        self.chips_widget.setVisible(False)
        main_layout.insertWidget(3, self.chips_widget)

        self.reset_button.setText("Очистить")
        self.reset_button.setVisible(False)

        self.summary_homework_waiting = self.summary_homework
        self.summary_homework_review = QLabel()
        summary_layout.insertWidget(
            max(0, summary_layout.count() - 1),
            self.summary_homework_review,
        )
        for widget, tone in (
            (self.summary_lessons, "neutral"),
            (self.summary_students, "neutral"),
            (self.summary_paid, "success"),
            (self.summary_unpaid, "error"),
            (self.summary_homework_waiting, "warning"),
            (self.summary_homework_review, "warning"),
            (self.summary_attention, "error"),
        ):
            widget.setObjectName("journalSummaryPill")
            widget.setProperty("tone", tone)

        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Когда", "Ученик", "Занятие", "Статус", "Оплата", "ДЗ", "Ресурсы"]
        )
        self.table.verticalHeader().setDefaultSectionSize(50)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column in (3, 4, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        self.payment_filter.currentIndexChanged.connect(
            lambda *_args: self._resolve_smart_conflict("payment")
        )
        self.homework_filter.currentIndexChanged.connect(
            lambda *_args: self._resolve_smart_conflict("homework")
        )

    def _install_period_presets(self) -> None:
        existing = {
            str(self.period_filter.itemData(index))
            for index in range(self.period_filter.count())
        }
        additions = (
            ("Этот месяц", "this_month"),
            ("Сегодня", "today"),
            ("Эта неделя", "this_week"),
            ("Прошлая неделя", "last_week"),
        )
        for label, value in reversed(additions):
            if value not in existing:
                self.period_filter.insertItem(0, label, value)

    @staticmethod
    def _month_bounds(today: date) -> tuple[date, date]:
        last = calendar.monthrange(today.year, today.month)[1]
        return today.replace(day=1), today.replace(day=last)

    def _preset_bounds(self) -> tuple[date, date]:
        today = date.today()
        preset = str(self.period_filter.currentData() or "this_month")
        if preset == "today":
            return today, today
        if preset == "this_week":
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=6)
        if preset == "last_week":
            end = today - timedelta(days=today.weekday() + 1)
            return end - timedelta(days=6), end
        if preset == "this_month":
            return self._month_bounds(today)
        return super()._preset_bounds()

    def _restore_ux_state(self, state: dict[str, object]) -> None:
        try:
            view = JournalSmartView(str(state.get("smart_view", "all")))
        except ValueError:
            view = JournalSmartView.ALL
        self._set_smart_view(view)
        expanded = bool(state.get("advanced_expanded", False))
        blocker = QSignalBlocker(self.filters_toggle)
        self.filters_toggle.setChecked(expanded)
        del blocker
        self._set_advanced_visible(expanded)

    def _set_advanced_visible(self, visible: bool) -> None:
        for widget in self._advanced_widgets:
            widget.setVisible(visible)

    def _advanced_toggled(self, expanded: bool) -> None:
        self._set_advanced_visible(expanded)
        self.filters_toggle.setAccessibleName(
            "Скрыть дополнительные фильтры журнала"
            if expanded
            else "Показать дополнительные фильтры журнала"
        )
        if not self._loading:
            self._persist_filters()

    def _apply_period_preset(self) -> None:
        super()._apply_period_preset()
        if self._ux_ready:
            custom = self.period_filter.currentData() == "custom"
            self.date_from.setVisible(custom)
            self.date_to.setVisible(custom)

    def _filters(self) -> LessonJournalFilter:
        filters = super()._filters()
        filters.attention_only = False
        if self.current_view == JournalSmartView.ATTENTION:
            filters.attention_only = True
        elif self.current_view == JournalSmartView.UNPAID:
            filters.payment = "unpaid"
        elif self.current_view == JournalSmartView.HOMEWORK_REVIEW:
            filters.homework = "review"
        return filters

    @staticmethod
    def _row_key(row: LessonJournalRow) -> str:
        lesson = row.lesson
        if lesson.occurrence_id is not None:
            return f"occurrence:{lesson.occurrence_id}"
        if lesson.rule_id is not None:
            return f"virtual:{lesson.rule_id}:{lesson.starts_at.isoformat()}"
        if lesson.lesson_id:
            return f"lesson:{lesson.lesson_id}"
        return f"logical:{lesson.student_id}:{lesson.starts_at.isoformat()}"

    def _capture_view_anchor(self, row_index: int | None = None) -> JournalViewAnchor | None:
        index = self.table.currentRow() if row_index is None else row_index
        if not (0 <= index < len(self._rows)):
            return None
        row = self._rows[index]
        return JournalViewAnchor(
            row_key=self._row_key(row),
            student_id=row.lesson.student_id,
            starts_at=row.lesson.starts_at,
            row_index=index,
            vertical_scroll=self.table.verticalScrollBar().value(),
        )

    def _restore_view_anchor(self, anchor: JournalViewAnchor) -> None:
        if not self._rows:
            self._clear_details()
            return
        target = -1
        for index, row in enumerate(self._rows):
            if self._row_key(row) == anchor.row_key:
                target = index
                break
        if target < 0:
            for index, row in enumerate(self._rows):
                if (
                    row.lesson.student_id == anchor.student_id
                    and row.lesson.starts_at == anchor.starts_at
                ):
                    target = index
                    break
        if target < 0:
            target = min(anchor.row_index, len(self._rows) - 1)
        self.table.selectRow(target)
        scrollbar = self.table.verticalScrollBar()
        value = min(anchor.vertical_scroll, scrollbar.maximum())
        scrollbar.setValue(value)
        QTimer.singleShot(
            0,
            lambda bar=scrollbar, saved=value: bar.setValue(min(saved, bar.maximum())),
        )

    def refresh(
        self,
        *,
        preserve_context: bool = False,
        anchor: JournalViewAnchor | None = None,
    ) -> None:
        if self._loading:
            return
        if preserve_context and anchor is None:
            anchor = self._capture_view_anchor()
        self._loading = True
        try:
            self._populate_filter_options()
            result = self.service.search(self._filters())
            self._render(result, anchor=anchor if preserve_context else None)
            self._persist_filters()
            if self._ux_ready:
                self._update_filter_ui()
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Журнал занятий", str(exc))
        finally:
            self._loading = False

    def _render(
        self,
        result: LessonJournalResult,
        *,
        anchor: JournalViewAnchor | None = None,
    ) -> None:
        self._rows = list(result.rows)
        blocker = QSignalBlocker(self.table)
        self.table.setRowCount(len(self._rows))
        now = datetime.now()

        for row_index, row in enumerate(self._rows):
            lesson = row.lesson
            self.table.setItem(
                row_index,
                0,
                QTableWidgetItem(f"{lesson.starts_at:%d.%m.%Y}\n{lesson.starts_at:%H:%M}"),
            )
            self.table.setItem(row_index, 1, QTableWidgetItem(lesson.student_name))

            topic = lesson.topic or "Без темы"
            lesson_item = QTableWidgetItem(f"{subject_label(lesson.subject)} · {topic}")
            lesson_item.setToolTip(f"{subject_label(lesson.subject)}\n{topic}")
            self.table.setItem(row_index, 2, lesson_item)

            status_text = LESSON_STATUS_LABELS.get(lesson.status, lesson.status)
            if row.requires_attention and lesson.status != "cancelled":
                status_text = f"⚠ {status_text}"
            status_item = QTableWidgetItem(status_text)
            if row.requires_attention and lesson.status != "cancelled":
                status_item.setForeground(QColor("#8A5A00"))
            self.table.setItem(row_index, 3, status_item)

            rate = f"{lesson.rate_cents / 100:,.0f} ₽" if lesson.rate_cents else "—"
            payment = QTableWidgetItem(rate)
            payment.setCheckState(
                Qt.CheckState.Checked if lesson.paid else Qt.CheckState.Unchecked
            )
            payment.setData(Qt.ItemDataRole.UserRole, row_index)
            payment.setToolTip(f"{'Оплачено' if lesson.paid else 'Не оплачено'} · {rate}")
            if lesson.status == "cancelled":
                payment.setFlags(payment.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            elif not lesson.paid and lesson.ends_at < now:
                payment.setBackground(QColor("#FFF0F0"))
                payment.setForeground(QColor("#9B2C2C"))
            self.table.setItem(row_index, 4, payment)

            homework = QComboBox()
            for status in HomeworkStatus:
                homework.addItem(COMPACT_HOMEWORK_LABELS[status], status.value)
            homework.setCurrentIndex(
                max(0, homework.findData(row.homework_status.value))
            )
            homework.setEnabled(lesson.status != "cancelled")
            homework.setProperty("journalRow", row_index)
            homework.setAccessibleName(
                f"Статус домашней работы: {lesson.student_name}, "
                f"{lesson.starts_at:%d.%m.%Y}"
            )
            homework.currentIndexChanged.connect(
                lambda _index, current=lesson, combo=homework: self._homework_changed(
                    current,
                    combo,
                )
            )
            self.table.setCellWidget(row_index, 5, homework)

            resources = (
                ("З", row.recording_exists, "Запись"),
                ("Т", row.transcript_exists, "Транскрипт"),
                ("М", row.materials_exist, "Материалы"),
            )
            resource_text = "  ".join(
                f"{short}{'✓' if ready else '—'}"
                for short, ready, _label in resources
            )
            description = " · ".join(
                f"{label}: {'есть' if ready else 'нет'}"
                for _short, ready, label in resources
            )
            resource_item = QTableWidgetItem(resource_text)
            resource_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            resource_item.setToolTip(description)
            self.table.setItem(row_index, 6, resource_item)
        del blocker

        summary = result.summary
        self.found_label.setText(f"Найдено: {result.total}")
        self.summary_lessons.setText(f"{summary.lessons} занятий")
        self.summary_students.setText(f"{summary.students} учеников")
        self.summary_paid.setText(f"{summary.paid_cents / 100:,.0f} ₽ оплачено")
        if self._ux_ready:
            self.summary_unpaid.setText(f"{summary.unpaid_cents / 100:,.0f} ₽ долг")
            self.summary_homework_waiting.setText(
                f"{summary.homework_waiting} ДЗ ожидается"
            )
            self.summary_homework_review.setText(
                f"{summary.homework_review} ДЗ проверить"
            )
            self.summary_attention.setText(f"{summary.attention} требуют внимания")
            self.summary_unpaid.setVisible(summary.unpaid_cents > 0)
            self.summary_homework_waiting.setVisible(summary.homework_waiting > 0)
            self.summary_homework_review.setVisible(summary.homework_review > 0)
            self.summary_attention.setVisible(summary.attention > 0)
        else:
            self.summary_unpaid.setText(f"Долг · {summary.unpaid_cents / 100:,.0f} ₽")
            self.summary_homework.setText(
                f"ДЗ: ждём {summary.homework_waiting} · проверить "
                f"{summary.homework_review}"
            )
            self.summary_attention.setText(f"Внимание · {summary.attention}")

        self.more_button.setVisible(result.has_more)
        if self._rows:
            if anchor is not None:
                self._restore_view_anchor(anchor)
            else:
                self.table.selectRow(0)
        else:
            self._clear_details()

    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 4:
            return
        stored_index = item.data(Qt.ItemDataRole.UserRole)
        row_index = int(stored_index) if stored_index is not None else -1
        if not (0 <= row_index < len(self._rows)):
            return
        row = self._rows[row_index]
        paid = item.checkState() == Qt.CheckState.Checked
        if paid == row.lesson.paid:
            return
        anchor = self._capture_view_anchor(row_index)
        try:
            self.service.set_paid(row.lesson, paid)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Оплата занятия", str(exc))
        self.refresh(preserve_context=True, anchor=anchor)

    def _homework_changed(self, lesson, combo: QComboBox) -> None:
        if self._loading:
            return
        row_index = int(combo.property("journalRow") or 0)
        anchor = self._capture_view_anchor(row_index)
        try:
            self.service.set_homework_status(
                lesson,
                HomeworkStatus(str(combo.currentData())),
            )
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Домашняя работа", str(exc))
        self.refresh(preserve_context=True, anchor=anchor)

    def _detail_payment_changed(self, paid: bool) -> None:
        anchor = self._capture_view_anchor()
        super()._detail_payment_changed(paid)
        if anchor is not None:
            self._restore_view_anchor(anchor)

    def _detail_homework_changed(self, index: int) -> None:
        anchor = self._capture_view_anchor()
        super()._detail_homework_changed(index)
        if anchor is not None:
            self._restore_view_anchor(anchor)

    def _save_due(self) -> None:
        anchor = self._capture_view_anchor()
        super()._save_due()
        if anchor is not None:
            self._restore_view_anchor(anchor)

    def _show_more(self) -> None:
        anchor = self._capture_view_anchor()
        self._visible_limit += self.page_size
        self.refresh(preserve_context=True, anchor=anchor)

    def _set_smart_view(self, view: JournalSmartView) -> None:
        self.current_view = view
        button = self._smart_buttons.get(view)
        if button is not None:
            button.setChecked(True)

    def apply_smart_view(self, view: str) -> None:
        try:
            selected = JournalSmartView(view)
        except ValueError:
            selected = JournalSmartView.ALL
        self._set_smart_view(selected)
        self._visible_limit = self.page_size
        if self._ux_ready:
            self._update_filter_ui()
        self.refresh()

    def _resolve_smart_conflict(self, kind: str) -> None:
        if self._loading:
            return
        if kind == "payment" and self.current_view == JournalSmartView.UNPAID:
            self._set_smart_view(JournalSmartView.ALL)
        elif kind == "homework" and self.current_view == JournalSmartView.HOMEWORK_REVIEW:
            self._set_smart_view(JournalSmartView.ALL)
        self._update_filter_ui()

    def _schedule_refresh(self, *_args) -> None:
        if self._loading:
            return
        self._visible_limit = self.page_size
        if self._ux_ready:
            self._update_filter_ui()
        self._debounce.start()

    def _advanced_filter_count(self) -> int:
        values = (
            str(self.payment_filter.currentData() or "all") != "all",
            str(self.homework_filter.currentData() or "all") != "all",
            bool(self.status_filter.currentData()),
            self.time_enabled.isChecked(),
            bool(self.processing_filter.currentData()),
            bool(self.recording_filter.currentData()),
            bool(self.transcript_filter.currentData()),
            bool(self.materials_filter.currentData()),
            str(self.sort_filter.currentData() or "date_desc") != "date_desc",
        )
        return sum(values)

    def _has_user_filters(self) -> bool:
        return bool(
            self.search.text().strip()
            or self.student_filter.currentData()
            or self.subject_filter.currentData()
            or str(self.period_filter.currentData() or "this_month") != "this_month"
            or self._advanced_filter_count()
            or self.current_view != JournalSmartView.ALL
        )

    def _update_filter_ui(self) -> None:
        count = self._advanced_filter_count()
        self.filters_toggle.setText(f"Фильтры · {count}" if count else "Фильтры")
        self.reset_button.setVisible(self._has_user_filters())
        self._rebuild_filter_chips()

    def _clear_chip_layout(self) -> None:
        while self.chips_layout.count():
            item = self.chips_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._chip_buttons.clear()

    def _add_filter_chip(self, label: str, resetter) -> None:
        button = QPushButton(f"{label}  ×")
        button.setObjectName("journalFilterChip")
        button.setAccessibleName(f"Удалить фильтр «{label}»")
        button.clicked.connect(lambda _checked=False, action=resetter: action())
        self.chips_layout.addWidget(button)
        self._chip_buttons.append(button)

    def _rebuild_filter_chips(self) -> None:
        self._clear_chip_layout()
        if self.search.text().strip():
            query = self.search.text().strip()
            display = query if len(query) <= 24 else f"{query[:21]}…"
            self._add_filter_chip(f"Поиск: {display}", self.search.clear)
        if self.student_filter.currentData():
            self._add_filter_chip(
                self.student_filter.currentText(),
                lambda: self.student_filter.setCurrentIndex(0),
            )
        if self.subject_filter.currentData():
            self._add_filter_chip(
                self.subject_filter.currentText(),
                lambda: self.subject_filter.setCurrentIndex(0),
            )
        period = str(self.period_filter.currentData() or "this_month")
        if period != "this_month":
            self._add_filter_chip(
                self.period_filter.currentText(),
                lambda: self.period_filter.setCurrentIndex(
                    self.period_filter.findData("this_month")
                ),
            )
        if str(self.payment_filter.currentData() or "all") != "all":
            self._add_filter_chip(
                self.payment_filter.currentText(),
                lambda: self.payment_filter.setCurrentIndex(0),
            )
        if str(self.homework_filter.currentData() or "all") != "all":
            self._add_filter_chip(
                self.homework_filter.currentText(),
                lambda: self.homework_filter.setCurrentIndex(0),
            )
        if self.status_filter.currentData():
            self._add_filter_chip(
                self.status_filter.currentText(),
                lambda: self.status_filter.setCurrentIndex(0),
            )
        if self.time_enabled.isChecked():
            self._add_filter_chip(
                f"{self.time_from.time().toString('HH:mm')}–"
                f"{self.time_to.time().toString('HH:mm')}",
                lambda: self.time_enabled.setChecked(False),
            )
        for combo in (
            self.processing_filter,
            self.recording_filter,
            self.transcript_filter,
            self.materials_filter,
        ):
            if combo.currentData():
                self._add_filter_chip(
                    combo.currentText(),
                    lambda current=combo: current.setCurrentIndex(0),
                )
        if str(self.sort_filter.currentData() or "date_desc") != "date_desc":
            self._add_filter_chip(
                self.sort_filter.currentText(),
                lambda: self.sort_filter.setCurrentIndex(
                    self.sort_filter.findData("date_desc")
                ),
            )
        self.chips_layout.addStretch(1)
        self.chips_widget.setVisible(bool(self._chip_buttons))

    def reset_filters(self) -> None:
        self._loading = True
        try:
            self.search.clear()
            self.student_filter.setCurrentIndex(0)
            self.subject_filter.setCurrentIndex(0)
            self.period_filter.setCurrentIndex(
                max(0, self.period_filter.findData("this_month"))
            )
            self.payment_filter.setCurrentIndex(0)
            self.homework_filter.setCurrentIndex(0)
            self.status_filter.setCurrentIndex(0)
            self.processing_filter.setCurrentIndex(0)
            self.recording_filter.setCurrentIndex(0)
            self.transcript_filter.setCurrentIndex(0)
            self.materials_filter.setCurrentIndex(0)
            self.attention_only.setChecked(False)
            self.time_enabled.setChecked(False)
            self.sort_filter.setCurrentIndex(
                max(0, self.sort_filter.findData("date_desc"))
            )
            self._set_smart_view(JournalSmartView.ALL)
            self._visible_limit = self.page_size
            self._apply_period_preset()
        finally:
            self._loading = False
        self._update_filter_ui()
        self.refresh()

    def filter_state(self) -> dict[str, object]:
        state = super().filter_state()
        state["smart_view"] = self.current_view.value
        state["advanced_expanded"] = bool(
            getattr(self, "filters_toggle", None)
            and self.filters_toggle.isChecked()
        )
        return state

    def restore_filter_state(self, state: dict[str, object]) -> None:
        super().restore_filter_state(state)
        if not self._ux_ready:
            return
        self._restore_ux_state(state)
        self.search.clear()
        self._update_filter_ui()

    def _persist_filters(self) -> None:
        state = self.filter_state()
        state.pop("query", None)
        self.settings.setValue(
            "ux6/journal/filters",
            json.dumps(state, ensure_ascii=False),
        )

    def showEvent(self, event) -> None:
        QWidget.showEvent(self, event)
        if self.isVisible():
            anchor = self._capture_view_anchor()
            QTimer.singleShot(
                0,
                lambda saved=anchor: self.refresh(
                    preserve_context=saved is not None,
                    anchor=saved,
                ),
            )
