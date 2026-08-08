from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from PySide6.QtCore import (
    QDate,
    QDateTime,
    QSettings,
    QSignalBlocker,
    Qt,
    QTime,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..crm import CrmStore, ScheduledLesson
from ..lesson_journal import (
    HomeworkStatus,
    LessonJournalFilter,
    LessonJournalResult,
    LessonJournalRow,
    LessonJournalService,
)
from .localization import subject_label
from .theme import set_button_kind

HOMEWORK_LABELS = {
    HomeworkStatus.NONE: "Без ДЗ",
    HomeworkStatus.ASSIGNED: "Назначено",
    HomeworkStatus.SENT: "Отправлено",
    HomeworkStatus.RECEIVED: "Получено",
    HomeworkStatus.CHECKED: "Проверено",
    HomeworkStatus.RETURNED: "Обратная связь",
}
LESSON_STATUS_LABELS = {
    "planned": "Запланировано",
    "in_progress": "Идёт занятие",
    "completed": "Завершено",
    "cancelled": "Отменено",
    "recording_failed": "Ошибка записи",
}
PROCESSING_STATUS_LABELS = {
    "draft": "Черновик",
    "recording": "Запись",
    "recorded": "Аудио готово",
    "transcribing": "Транскрибация",
    "review_required": "Проверка транскрипта",
    "ready": "Готов к публикации",
    "published": "Опубликовано",
    "generated_tex": "LaTeX готов",
    "compiling_pdf": "Сборка PDF",
    "pdf_review_required": "Проверка PDF",
    "compile_failed": "Ошибка PDF",
    "generating": "Формирование материалов",
    "completed": "Обработано",
    "failed": "Ошибка обработки",
}


class LessonJournalPage(QWidget):
    open_lesson_requested = Signal(str)
    open_materials_requested = Signal(str)
    show_in_schedule_requested = Signal(object)

    page_size = 100

    def __init__(
        self,
        store: CrmStore,
        *,
        lesson_store: object | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.service = LessonJournalService(store, lesson_store)
        self.settings = QSettings("TutorAssistant", "TutorAssistant")
        self._rows: list[LessonJournalRow] = []
        self._loading = False
        self._loading_detail = False
        self._visible_limit = self.page_size
        self._build()
        self._populate_filter_options()
        self._apply_period_preset()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self.refresh)
        self._connect_filters()
        if not self._restore_persisted_filters():
            self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(10)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Журнал занятий")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "История уроков, оплата, домашняя работа и связанные материалы в одном представлении"
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box, 1)
        self.reset_button = set_button_kind(QPushButton("Сбросить фильтры"), "ghost")
        self.reset_button.clicked.connect(self.reset_filters)
        heading.addWidget(self.reset_button)
        layout.addLayout(heading)

        smart = QHBoxLayout()
        smart.addWidget(QLabel("Быстрые представления:"))
        for key, label in (
            ("all", "Все"),
            ("attention", "Требует внимания"),
            ("unpaid", "Неоплаченные"),
            ("homework_review", "ДЗ на проверку"),
        ):
            button = set_button_kind(QPushButton(label), "ghost")
            button.setProperty("journalView", key)
            button.clicked.connect(lambda _checked=False, view=key: self.apply_smart_view(view))
            smart.addWidget(button)
        smart.addStretch(1)
        self.found_label = QLabel()
        self.found_label.setObjectName("muted")
        smart.addWidget(self.found_label)
        layout.addLayout(smart)

        filter_panel = QFrame()
        filter_panel.setObjectName("infoPanel")
        filter_layout = QVBoxLayout(filter_panel)
        filter_layout.setContentsMargins(12, 10, 12, 10)
        filter_layout.setSpacing(8)

        row1 = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по ученику, теме, предмету или lesson ID")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Поиск по журналу занятий")
        row1.addWidget(self.search, 2)

        self.student_filter = QComboBox()
        self.student_filter.setMinimumWidth(180)
        self.student_filter.setAccessibleName("Фильтр журнала по ученику")
        row1.addWidget(self.student_filter)

        self.subject_filter = QComboBox()
        self.subject_filter.setMinimumWidth(150)
        self.subject_filter.setAccessibleName("Фильтр журнала по предмету")
        row1.addWidget(self.subject_filter)

        self.period_filter = QComboBox()
        for label, value in (
            ("Учебный год", "academic"),
            ("Последние 30 дней", "last30"),
            ("Последние 90 дней", "last90"),
            ("Следующие 30 дней", "next30"),
            ("Произвольный период", "custom"),
        ):
            self.period_filter.addItem(label, value)
        row1.addWidget(self.period_filter)

        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("dd.MM.yyyy")
        row1.addWidget(self.date_from)
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("dd.MM.yyyy")
        row1.addWidget(self.date_to)
        filter_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.payment_filter = QComboBox()
        for label, value in (
            ("Любая оплата", "all"),
            ("Оплачено", "paid"),
            ("Не оплачено", "unpaid"),
            ("Просрочена оплата", "unpaid_past"),
        ):
            self.payment_filter.addItem(label, value)
        row2.addWidget(self.payment_filter)

        self.homework_filter = QComboBox()
        self.homework_filter.addItem("Любое ДЗ", "all")
        for status in HomeworkStatus:
            self.homework_filter.addItem(HOMEWORK_LABELS[status], status.value)
        self.homework_filter.addItem("Требует проверки", "review")
        self.homework_filter.addItem("Просрочено", "overdue")
        row2.addWidget(self.homework_filter)

        self.status_filter = QComboBox()
        self.status_filter.addItem("Любой статус занятия", "")
        for value, label in LESSON_STATUS_LABELS.items():
            self.status_filter.addItem(label, value)
        row2.addWidget(self.status_filter)

        self.time_enabled = QCheckBox("Время")
        row2.addWidget(self.time_enabled)
        self.time_from = QTimeEdit(QTime(8, 0))
        self.time_from.setDisplayFormat("HH:mm")
        self.time_from.setEnabled(False)
        row2.addWidget(self.time_from)
        self.time_to = QTimeEdit(QTime(22, 0))
        self.time_to.setDisplayFormat("HH:mm")
        self.time_to.setEnabled(False)
        row2.addWidget(self.time_to)

        self.attention_only = QCheckBox("Требует внимания")
        row2.addWidget(self.attention_only)

        self.sort_filter = QComboBox()
        for label, value in (
            ("Сначала новые", "date_desc"),
            ("Сначала старые", "date_asc"),
            ("По ученику", "student"),
            ("По предмету", "subject"),
            ("По оплате", "payment"),
            ("По статусу ДЗ", "homework"),
        ):
            self.sort_filter.addItem(label, value)
        row2.addWidget(self.sort_filter)
        row2.addStretch(1)
        filter_layout.addLayout(row2)

        row3 = QHBoxLayout()
        self.processing_filter = QComboBox()
        self.processing_filter.addItem("Любая обработка", "")
        for value, label in PROCESSING_STATUS_LABELS.items():
            self.processing_filter.addItem(label, value)
        row3.addWidget(self.processing_filter)

        self.recording_filter = QComboBox()
        self.recording_filter.addItem("Любая запись", "")
        self.recording_filter.addItem("Есть запись", "yes")
        self.recording_filter.addItem("Без записи", "no")
        row3.addWidget(self.recording_filter)

        self.transcript_filter = QComboBox()
        self.transcript_filter.addItem("Любой транскрипт", "")
        self.transcript_filter.addItem("Есть транскрипт", "yes")
        self.transcript_filter.addItem("Без транскрипта", "no")
        row3.addWidget(self.transcript_filter)

        self.materials_filter = QComboBox()
        self.materials_filter.addItem("Любые материалы", "")
        self.materials_filter.addItem("Есть материалы", "yes")
        self.materials_filter.addItem("Без материалов", "no")
        row3.addWidget(self.materials_filter)
        row3.addStretch(1)
        filter_layout.addLayout(row3)
        layout.addWidget(filter_panel)

        summary = QHBoxLayout()
        self.summary_lessons = QLabel()
        self.summary_students = QLabel()
        self.summary_paid = QLabel()
        self.summary_unpaid = QLabel()
        self.summary_homework = QLabel()
        self.summary_attention = QLabel()
        for widget in (
            self.summary_lessons,
            self.summary_students,
            self.summary_paid,
            self.summary_unpaid,
            self.summary_homework,
            self.summary_attention,
        ):
            widget.setObjectName("statusPill")
            summary.addWidget(widget)
        summary.addStretch(1)
        layout.addLayout(summary)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("lessonJournalSplitter")
        splitter.setAccessibleName("Таблица и карточка занятия")

        self.table = QTableWidget(0, 12)
        self.table.setObjectName("lessonJournalTable")
        self.table.setHorizontalHeaderLabels(
            [
                "Дата",
                "Время",
                "Ученик",
                "Предмет",
                "Тема",
                "Статус",
                "Оплата",
                "ДЗ",
                "Запись",
                "Транскрипт",
                "Материалы",
                "Ставка",
            ]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemChanged.connect(self._table_item_changed)
        self.table.cellDoubleClicked.connect(self._row_activated)
        splitter.addWidget(self.table)

        details = QFrame()
        details.setObjectName("crmEditor")
        details.setMinimumWidth(300)
        details.setMaximumWidth(390)
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(16, 16, 16, 16)
        details_layout.setSpacing(10)
        self.detail_title = QLabel("Выберите занятие")
        self.detail_title.setObjectName("tileTitle")
        self.detail_title.setWordWrap(True)
        details_layout.addWidget(self.detail_title)
        self.detail_context = QLabel("Карточка покажет оплату, ДЗ и связанные материалы.")
        self.detail_context.setObjectName("muted")
        self.detail_context.setWordWrap(True)
        details_layout.addWidget(self.detail_context)

        self.detail_payment = QCheckBox("Оплачено")
        self.detail_payment.setEnabled(False)
        self.detail_payment.toggled.connect(self._detail_payment_changed)
        details_layout.addWidget(self.detail_payment)

        self.detail_homework = QComboBox()
        for status in HomeworkStatus:
            self.detail_homework.addItem(HOMEWORK_LABELS[status], status.value)
        self.detail_homework.setEnabled(False)
        self.detail_homework.currentIndexChanged.connect(self._detail_homework_changed)
        details_layout.addWidget(self.detail_homework)

        due_row = QHBoxLayout()
        self.due_enabled = QCheckBox("Дедлайн")
        self.due_enabled.setEnabled(False)
        self.due_enabled.toggled.connect(self._due_enabled_changed)
        due_row.addWidget(self.due_enabled)
        self.due_at = QDateTimeEdit(QDateTime.currentDateTime().addDays(7))
        self.due_at.setCalendarPopup(True)
        self.due_at.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.due_at.setEnabled(False)
        due_row.addWidget(self.due_at, 1)
        details_layout.addLayout(due_row)
        self.save_due_button = set_button_kind(QPushButton("Сохранить срок ДЗ"), "ghost")
        self.save_due_button.setEnabled(False)
        self.save_due_button.clicked.connect(self._save_due)
        details_layout.addWidget(self.save_due_button)

        self.detail_processing = QLabel()
        self.detail_processing.setObjectName("muted")
        self.detail_processing.setWordWrap(True)
        details_layout.addWidget(self.detail_processing)
        details_layout.addStretch(1)

        self.open_lesson_button = set_button_kind(QPushButton("Открыть занятие"), "primary")
        self.open_lesson_button.setEnabled(False)
        self.open_lesson_button.clicked.connect(self._open_selected_lesson)
        details_layout.addWidget(self.open_lesson_button)
        self.open_materials_button = set_button_kind(QPushButton("Открыть материалы"), "ghost")
        self.open_materials_button.setEnabled(False)
        self.open_materials_button.clicked.connect(self._open_selected_materials)
        details_layout.addWidget(self.open_materials_button)
        self.open_schedule_button = set_button_kind(QPushButton("Показать в расписании"), "ghost")
        self.open_schedule_button.setEnabled(False)
        self.open_schedule_button.clicked.connect(self._open_selected_schedule)
        details_layout.addWidget(self.open_schedule_button)
        splitter.addWidget(details)
        splitter.setSizes([900, 330])
        layout.addWidget(splitter, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.more_button = set_button_kind(QPushButton("Показать ещё"), "ghost")
        self.more_button.clicked.connect(self._show_more)
        footer.addWidget(self.more_button)
        layout.addLayout(footer)

    def _populate_filter_options(self) -> None:
        student = self.student_filter.currentData()
        subject = self.subject_filter.currentData()
        self.student_filter.blockSignals(True)
        self.student_filter.clear()
        self.student_filter.addItem("Все ученики", "")
        for profile in self.store.list_students(include_archived=True):
            self.student_filter.addItem(profile.full_name, profile.id)
        if student:
            index = self.student_filter.findData(student)
            if index >= 0:
                self.student_filter.setCurrentIndex(index)
        self.student_filter.blockSignals(False)

        subjects = {
            value
            for profile in self.store.list_students(include_archived=True)
            for value in profile.subjects
            if value
        }
        subjects.update(rule.subject for rule in self.store.list_schedule_rules() if rule.subject)
        self.subject_filter.blockSignals(True)
        self.subject_filter.clear()
        self.subject_filter.addItem("Все предметы", "")
        for value in sorted(subjects, key=lambda item: subject_label(item).casefold()):
            self.subject_filter.addItem(subject_label(value), value)
        if subject:
            index = self.subject_filter.findData(subject)
            if index >= 0:
                self.subject_filter.setCurrentIndex(index)
        self.subject_filter.blockSignals(False)

    def _connect_filters(self) -> None:
        self.search.textChanged.connect(self._schedule_refresh)
        for combo in (
            self.student_filter,
            self.subject_filter,
            self.payment_filter,
            self.homework_filter,
            self.status_filter,
            self.processing_filter,
            self.recording_filter,
            self.transcript_filter,
            self.materials_filter,
            self.sort_filter,
        ):
            combo.currentIndexChanged.connect(self._schedule_refresh)
        self.period_filter.currentIndexChanged.connect(self._period_changed)
        self.date_from.dateChanged.connect(self._custom_date_changed)
        self.date_to.dateChanged.connect(self._custom_date_changed)
        self.attention_only.toggled.connect(self._schedule_refresh)
        self.time_enabled.toggled.connect(self._time_enabled_changed)
        self.time_from.timeChanged.connect(self._schedule_refresh)
        self.time_to.timeChanged.connect(self._schedule_refresh)

    def _schedule_refresh(self, *_args) -> None:
        if self._loading:
            return
        self._visible_limit = self.page_size
        self._debounce.start()

    @staticmethod
    def _academic_bounds(today: date) -> tuple[date, date]:
        start_year = today.year if today.month >= 8 else today.year - 1
        return date(start_year, 8, 1), date(start_year + 1, 7, 31)

    def _preset_bounds(self) -> tuple[date, date]:
        today = date.today()
        preset = str(self.period_filter.currentData() or "academic")
        if preset == "last30":
            return today - timedelta(days=30), today
        if preset == "last90":
            return today - timedelta(days=90), today
        if preset == "next30":
            return today, today + timedelta(days=30)
        if preset == "custom":
            return self._qdate_to_date(self.date_from.date()), self._qdate_to_date(
                self.date_to.date()
            )
        return self._academic_bounds(today)

    def _apply_period_preset(self) -> None:
        start, end = self._preset_bounds()
        custom = self.period_filter.currentData() == "custom"
        blocker_from = QSignalBlocker(self.date_from)
        blocker_to = QSignalBlocker(self.date_to)
        self.date_from.setDate(QDate(start.year, start.month, start.day))
        self.date_to.setDate(QDate(end.year, end.month, end.day))
        self.date_from.setEnabled(custom)
        self.date_to.setEnabled(custom)
        del blocker_from, blocker_to

    def _period_changed(self, *_args) -> None:
        if self._loading:
            return
        self._apply_period_preset()
        self._schedule_refresh()

    def _custom_date_changed(self, *_args) -> None:
        if self._loading or self.period_filter.currentData() != "custom":
            return
        self._schedule_refresh()

    def _time_enabled_changed(self, enabled: bool) -> None:
        self.time_from.setEnabled(enabled)
        self.time_to.setEnabled(enabled)
        self._schedule_refresh()

    @staticmethod
    def _qdate_to_date(value: QDate) -> date:
        return date(value.year(), value.month(), value.day())

    @staticmethod
    def _qtime_to_minute(value: QTime) -> int:
        return value.hour() * 60 + value.minute()

    @staticmethod
    def _optional_bool(combo: QComboBox) -> bool | None:
        value = str(combo.currentData() or "")
        if value == "yes":
            return True
        if value == "no":
            return False
        return None

    def _filters(self) -> LessonJournalFilter:
        start, end = self._preset_bounds()
        return LessonJournalFilter(
            query=self.search.text().strip(),
            student_id=str(self.student_filter.currentData() or "") or None,
            subject=str(self.subject_filter.currentData() or "") or None,
            date_from=start,
            date_to=end,
            payment=str(self.payment_filter.currentData() or "all"),
            lesson_status=str(self.status_filter.currentData() or "") or None,
            homework=str(self.homework_filter.currentData() or "all"),
            processing_status=str(self.processing_filter.currentData() or "") or None,
            recording=self._optional_bool(self.recording_filter),
            transcript=self._optional_bool(self.transcript_filter),
            materials=self._optional_bool(self.materials_filter),
            attention_only=self.attention_only.isChecked(),
            time_from_minute=(
                self._qtime_to_minute(self.time_from.time())
                if self.time_enabled.isChecked()
                else None
            ),
            time_to_minute=(
                self._qtime_to_minute(self.time_to.time())
                if self.time_enabled.isChecked()
                else None
            ),
            sort=str(self.sort_filter.currentData() or "date_desc"),
            limit=self._visible_limit,
        )

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            self._populate_filter_options()
            result = self.service.search(self._filters())
            self._render(result)
            self._persist_filters()
        except Exception as exc:
            QMessageBox.critical(self, "Журнал занятий", str(exc))
        finally:
            self._loading = False

    def _render(self, result: LessonJournalResult) -> None:
        self._rows = list(result.rows)
        blocker = QSignalBlocker(self.table)
        self.table.setRowCount(len(self._rows))
        now = datetime.now()
        for row_index, row in enumerate(self._rows):
            lesson = row.lesson
            values = (
                lesson.starts_at.strftime("%d.%m.%Y"),
                lesson.starts_at.strftime("%H:%M"),
                lesson.student_name,
                subject_label(lesson.subject),
                lesson.topic or "—",
                LESSON_STATUS_LABELS.get(lesson.status, lesson.status),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if row.requires_attention and lesson.status != "cancelled":
                    item.setBackground(QColor("#FFF0F0"))
                if lesson.status == "cancelled":
                    item.setBackground(QColor("#F2F4F7"))
                self.table.setItem(row_index, column, item)

            payment = QTableWidgetItem("Оплачено" if lesson.paid else "Не оплачено")
            payment.setCheckState(
                Qt.CheckState.Checked if lesson.paid else Qt.CheckState.Unchecked
            )
            payment.setData(Qt.ItemDataRole.UserRole, row_index)
            if lesson.status == "cancelled":
                payment.setFlags(payment.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            elif not lesson.paid and lesson.ends_at < now:
                payment.setBackground(QColor("#FFF0F0"))
                payment.setForeground(QColor("#9B2C2C"))
            self.table.setItem(row_index, 6, payment)

            homework = QComboBox()
            for status in HomeworkStatus:
                homework.addItem(HOMEWORK_LABELS[status], status.value)
            index = homework.findData(row.homework_status.value)
            homework.setCurrentIndex(max(0, index))
            homework.setEnabled(lesson.status != "cancelled")
            homework.setAccessibleName(
                f"Статус домашней работы: {lesson.student_name}, {lesson.starts_at:%d.%m.%Y}"
            )
            homework.currentIndexChanged.connect(
                lambda _index, current=lesson, combo=homework: self._homework_changed(
                    current, combo
                )
            )
            self.table.setCellWidget(row_index, 7, homework)

            for column, ready in (
                (8, row.recording_exists),
                (9, row.transcript_exists),
                (10, row.materials_exist),
            ):
                marker = QTableWidgetItem("✓" if ready else "—")
                marker.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_index, column, marker)

            rate = f"{lesson.rate_cents / 100:,.0f} ₽" if lesson.rate_cents else "—"
            self.table.setItem(row_index, 11, QTableWidgetItem(rate))
        del blocker

        summary = result.summary
        self.found_label.setText(f"Найдено: {result.total}")
        self.summary_lessons.setText(f"Занятий · {summary.lessons}")
        self.summary_students.setText(f"Учеников · {summary.students}")
        self.summary_paid.setText(f"Оплачено · {summary.paid_cents / 100:,.0f} ₽")
        self.summary_unpaid.setText(f"Долг · {summary.unpaid_cents / 100:,.0f} ₽")
        self.summary_homework.setText(
            f"ДЗ: ждём {summary.homework_waiting} · проверить {summary.homework_review}"
        )
        self.summary_attention.setText(f"Внимание · {summary.attention}")
        self.more_button.setVisible(result.has_more)
        if self._rows:
            self.table.selectRow(0)
        else:
            self._clear_details()

    def _selected_row(self) -> LessonJournalRow | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def _selection_changed(self) -> None:
        current = self._selected_row()
        if current is None:
            self._clear_details()
            return
        self._loading_detail = True
        try:
            lesson = current.lesson
            self.detail_title.setText(
                f"{lesson.student_name}\n{lesson.starts_at:%d.%m.%Y · %H:%M}"
            )
            processing = PROCESSING_STATUS_LABELS.get(
                current.processing_status or "",
                current.processing_status or "Связанного lesson ID пока нет",
            )
            self.detail_context.setText(
                f"{subject_label(lesson.subject)} · {lesson.topic or 'Без темы'}\n"
                f"{LESSON_STATUS_LABELS.get(lesson.status, lesson.status)} · "
                f"{lesson.rate_cents / 100:,.0f} ₽"
            )
            self.detail_processing.setText(
                f"Обработка: {processing}\n"
                f"Запись: {'есть' if current.recording_exists else '—'} · "
                f"Транскрипт: {'есть' if current.transcript_exists else '—'} · "
                f"Материалы: {'есть' if current.materials_exist else '—'}"
            )
            self.detail_payment.setEnabled(lesson.status != "cancelled")
            self.detail_payment.setChecked(lesson.paid)
            self.detail_homework.setEnabled(lesson.status != "cancelled")
            self.detail_homework.setCurrentIndex(
                max(0, self.detail_homework.findData(current.homework_status.value))
            )
            self.due_enabled.setEnabled(lesson.status != "cancelled")
            self.save_due_button.setEnabled(lesson.status != "cancelled")
            if current.homework_due_at is not None:
                due = current.homework_due_at
                self.due_enabled.setChecked(True)
                self.due_at.setDateTime(
                    QDateTime(
                        QDate(due.year, due.month, due.day),
                        QTime(due.hour, due.minute, due.second),
                    )
                )
            else:
                self.due_enabled.setChecked(False)
                default_due = lesson.starts_at + timedelta(days=7)
                self.due_at.setDateTime(
                    QDateTime(
                        QDate(default_due.year, default_due.month, default_due.day),
                        QTime(default_due.hour, default_due.minute),
                    )
                )
            self.due_at.setEnabled(self.due_enabled.isChecked())
            self.open_lesson_button.setEnabled(bool(lesson.lesson_id))
            self.open_materials_button.setEnabled(
                bool(lesson.lesson_id or current.materials_exist)
            )
            self.open_schedule_button.setEnabled(True)
        finally:
            self._loading_detail = False

    def _clear_details(self) -> None:
        self._loading_detail = True
        try:
            self.detail_title.setText("Выберите занятие")
            self.detail_context.setText(
                "Карточка покажет оплату, ДЗ и связанные материалы."
            )
            self.detail_processing.clear()
            for widget in (
                self.detail_payment,
                self.detail_homework,
                self.due_enabled,
                self.due_at,
                self.save_due_button,
                self.open_lesson_button,
                self.open_materials_button,
                self.open_schedule_button,
            ):
                widget.setEnabled(False)
        finally:
            self._loading_detail = False

    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 6:
            return
        stored_index = item.data(Qt.ItemDataRole.UserRole)
        row_index = int(stored_index) if stored_index is not None else -1
        if not (0 <= row_index < len(self._rows)):
            return
        row = self._rows[row_index]
        paid = item.checkState() == Qt.CheckState.Checked
        if paid == row.lesson.paid:
            return
        try:
            self.service.set_paid(row.lesson, paid)
        except Exception as exc:
            QMessageBox.critical(self, "Оплата занятия", str(exc))
        self.refresh()

    def _homework_changed(self, lesson: ScheduledLesson, combo: QComboBox) -> None:
        if self._loading:
            return
        try:
            self.service.set_homework_status(
                lesson,
                HomeworkStatus(str(combo.currentData())),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Домашняя работа", str(exc))
        self.refresh()

    def _detail_payment_changed(self, paid: bool) -> None:
        if self._loading_detail:
            return
        row = self._selected_row()
        if row is None or paid == row.lesson.paid:
            return
        try:
            self.service.set_paid(row.lesson, paid)
        except Exception as exc:
            QMessageBox.critical(self, "Оплата занятия", str(exc))
        self.refresh()

    def _detail_homework_changed(self, _index: int) -> None:
        if self._loading_detail:
            return
        row = self._selected_row()
        if row is None:
            return
        try:
            self.service.set_homework_status(
                row.lesson,
                HomeworkStatus(str(self.detail_homework.currentData())),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Домашняя работа", str(exc))
        self.refresh()

    def _due_enabled_changed(self, enabled: bool) -> None:
        if self._loading_detail:
            return
        self.due_at.setEnabled(enabled)

    @staticmethod
    def _qdatetime_to_datetime(value: QDateTime) -> datetime:
        day = value.date()
        clock = value.time()
        return datetime(
            day.year(),
            day.month(),
            day.day(),
            clock.hour(),
            clock.minute(),
            clock.second(),
        )

    def _save_due(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        due = (
            self._qdatetime_to_datetime(self.due_at.dateTime())
            if self.due_enabled.isChecked()
            else None
        )
        try:
            self.service.set_homework_due(row.lesson, due)
        except Exception as exc:
            QMessageBox.critical(self, "Домашняя работа", str(exc))
        self.refresh()

    def _row_activated(self, row: int, _column: int) -> None:
        if not (0 <= row < len(self._rows)):
            return
        current = self._rows[row]
        if current.lesson.lesson_id:
            self.open_lesson_requested.emit(current.lesson.lesson_id)
        else:
            self.show_in_schedule_requested.emit(current.lesson.starts_at)

    def _open_selected_lesson(self) -> None:
        row = self._selected_row()
        if row and row.lesson.lesson_id:
            self.open_lesson_requested.emit(row.lesson.lesson_id)

    def _open_selected_materials(self) -> None:
        row = self._selected_row()
        if row:
            self.open_materials_requested.emit(row.lesson.student_id)

    def _open_selected_schedule(self) -> None:
        row = self._selected_row()
        if row:
            self.show_in_schedule_requested.emit(row.lesson.starts_at)

    def _show_more(self) -> None:
        self._visible_limit += self.page_size
        self.refresh()

    def reset_filters(self) -> None:
        self._loading = True
        try:
            self.search.clear()
            self.student_filter.setCurrentIndex(0)
            self.subject_filter.setCurrentIndex(0)
            self.period_filter.setCurrentIndex(
                max(0, self.period_filter.findData("academic"))
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
            self._apply_period_preset()
            self._visible_limit = self.page_size
        finally:
            self._loading = False
        self.refresh()

    def apply_smart_view(self, view: str) -> None:
        self._loading = True
        try:
            self.payment_filter.setCurrentIndex(0)
            self.homework_filter.setCurrentIndex(0)
            self.attention_only.setChecked(False)
            if view == "attention":
                self.attention_only.setChecked(True)
            elif view == "unpaid":
                self.payment_filter.setCurrentIndex(
                    max(0, self.payment_filter.findData("unpaid_past"))
                )
            elif view == "homework_review":
                self.homework_filter.setCurrentIndex(
                    max(0, self.homework_filter.findData("review"))
                )
            self._visible_limit = self.page_size
        finally:
            self._loading = False
        self.refresh()

    def filter_student(self, student_id: str) -> None:
        index = self.student_filter.findData(student_id)
        if index >= 0:
            self.student_filter.setCurrentIndex(index)
            self.refresh()

    def filter_state(self) -> dict[str, object]:
        return {
            "query": self.search.text(),
            "student": str(self.student_filter.currentData() or ""),
            "subject": str(self.subject_filter.currentData() or ""),
            "period": str(self.period_filter.currentData() or "academic"),
            "date_from": self.date_from.date().toString("yyyy-MM-dd"),
            "date_to": self.date_to.date().toString("yyyy-MM-dd"),
            "payment": str(self.payment_filter.currentData() or "all"),
            "homework": str(self.homework_filter.currentData() or "all"),
            "status": str(self.status_filter.currentData() or ""),
            "processing": str(self.processing_filter.currentData() or ""),
            "recording": str(self.recording_filter.currentData() or ""),
            "transcript": str(self.transcript_filter.currentData() or ""),
            "materials": str(self.materials_filter.currentData() or ""),
            "attention": self.attention_only.isChecked(),
            "time_enabled": self.time_enabled.isChecked(),
            "time_from": self.time_from.time().toString("HH:mm"),
            "time_to": self.time_to.time().toString("HH:mm"),
            "sort": str(self.sort_filter.currentData() or "date_desc"),
        }

    @staticmethod
    def _restore_combo(combo: QComboBox, value: object) -> None:
        index = combo.findData(str(value or ""))
        if index >= 0:
            combo.setCurrentIndex(index)

    def restore_filter_state(self, state: dict[str, object]) -> None:
        self._loading = True
        try:
            self.search.setText(str(state.get("query", "")))
            self._restore_combo(self.student_filter, state.get("student"))
            self._restore_combo(self.subject_filter, state.get("subject"))
            self._restore_combo(self.period_filter, state.get("period", "academic"))
            self._restore_combo(self.payment_filter, state.get("payment", "all"))
            self._restore_combo(self.homework_filter, state.get("homework", "all"))
            self._restore_combo(self.status_filter, state.get("status", ""))
            self._restore_combo(self.processing_filter, state.get("processing", ""))
            self._restore_combo(self.recording_filter, state.get("recording", ""))
            self._restore_combo(self.transcript_filter, state.get("transcript", ""))
            self._restore_combo(self.materials_filter, state.get("materials", ""))
            self._restore_combo(self.sort_filter, state.get("sort", "date_desc"))
            self.attention_only.setChecked(bool(state.get("attention", False)))
            self.time_enabled.setChecked(bool(state.get("time_enabled", False)))
            for editor, key in (
                (self.date_from, "date_from"),
                (self.date_to, "date_to"),
            ):
                parsed = QDate.fromString(str(state.get(key, "")), "yyyy-MM-dd")
                if parsed.isValid():
                    editor.setDate(parsed)
            for editor, key in (
                (self.time_from, "time_from"),
                (self.time_to, "time_to"),
            ):
                parsed = QTime.fromString(str(state.get(key, "")), "HH:mm")
                if parsed.isValid():
                    editor.setTime(parsed)
            custom = self.period_filter.currentData() == "custom"
            self.date_from.setEnabled(custom)
            self.date_to.setEnabled(custom)
            self.time_from.setEnabled(self.time_enabled.isChecked())
            self.time_to.setEnabled(self.time_enabled.isChecked())
        finally:
            self._loading = False
        self.refresh()

    def _persist_filters(self) -> None:
        self.settings.setValue(
            "ux6/journal/filters",
            json.dumps(self.filter_state(), ensure_ascii=False),
        )

    def _restore_persisted_filters(self) -> bool:
        raw = self.settings.value("ux6/journal/filters", "")
        if not raw:
            return False
        try:
            state = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(state, dict):
            return False
        self.restore_filter_state(state)
        return True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.isVisible():
            QTimer.singleShot(0, self.refresh)
