from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TypeAlias

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..content import LessonContent, LessonFilters, LessonPage, StudentContentService
from ..content_browser import (
    content_file_rows,
    format_size,
    is_audio_path,
    pagination_text,
    resolve_known_path,
    status_label,
)
from ..domain import JobStatus, Student
from ..playback import PlaybackController, SegmentLoadResult, load_playback_segments
from .playback import PlaybackPanel, QtPlaybackBackend
from .theme import set_button_kind

BackgroundRunner: TypeAlias = Callable[
    [Callable[[], object], Callable[[object], None], Callable[[str], None]], None
]

KIND_LABELS = {
    "audio": "Аудио",
    "metadata": "Метаданные",
    "transcript": "Транскрипт",
    "document": "Документ",
    "other": "Файл",
}


class StudentContentPage(QWidget):
    status_changed = Signal(str, str)
    file_open_requested = Signal(object)

    def __init__(
        self,
        service: StudentContentService,
        students: list[Student],
        run_background: BackgroundRunner,
        playback_controller: PlaybackController,
        playback_backend: QtPlaybackBackend,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.workspace = service.workspace
        self.run_background = run_background
        self.playback_controller = playback_controller
        self.playback_backend = playback_backend
        self.page_size = 50
        self.offset = 0
        self.total = 0
        self._list_request = 0
        self._detail_request = 0
        self._initial_sync_started = False
        self._sync_running = False
        self._selected_lesson_id: str | None = None
        self._build()
        self.set_students(students)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self._filters_changed_now)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Материалы ученика")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Локальный архив занятий, аудиозаписей и подтверждённых транскриптов")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box, 1)
        self.sync_button = set_button_kind(QPushButton("Синхронизировать каталог"), "ghost")
        self.sync_button.setToolTip("Однократно проверить data/lessons и обновить индекс SQLite")
        self.sync_button.clicked.connect(self.synchronize)
        heading.addWidget(self.sync_button)
        self.refresh_button = set_button_kind(QPushButton("Обновить"), "primary")
        self.refresh_button.setToolTip("Обновить список из локальной базы без обхода файлов")
        self.refresh_button.clicked.connect(self.refresh)
        heading.addWidget(self.refresh_button)
        layout.addLayout(heading)

        filters = QFrame()
        filters.setObjectName("contentFilters")
        filters_layout = QGridLayout(filters)
        filters_layout.setContentsMargins(14, 10, 14, 10)
        filters_layout.setSpacing(9)
        self.student_filter = QComboBox()
        self.student_filter.setMinimumWidth(190)
        self.student_filter.setToolTip("Показать занятия выбранного ученика")
        filters_layout.addWidget(self.student_filter, 0, 0)
        self.subject_filter = QComboBox()
        self.subject_filter.setMinimumWidth(145)
        filters_layout.addWidget(self.subject_filter, 0, 1)
        self.status_filter = QComboBox()
        self.status_filter.addItem("Все статусы", None)
        for status in JobStatus:
            self.status_filter.addItem(status_label(status), status.value)
        self.status_filter.setMinimumWidth(170)
        filters_layout.addWidget(self.status_filter, 0, 2)
        self.period_enabled = QCheckBox("Период")
        filters_layout.addWidget(self.period_enabled, 0, 3)
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("dd.MM.yyyy")
        self.date_from.setDate(QDate.currentDate().addYears(-1))
        self.date_from.setEnabled(False)
        self.date_from.setMaximumWidth(120)
        filters_layout.addWidget(self.date_from, 0, 4)
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("dd.MM.yyyy")
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setEnabled(False)
        self.date_to.setMaximumWidth(120)
        filters_layout.addWidget(self.date_to, 0, 5)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Тема, ученик или предмет")
        self.search.setClearButtonEnabled(True)
        filters_layout.addWidget(self.search, 1, 0, 1, 5)
        self.reset_button = set_button_kind(QPushButton("Сбросить"), "ghost")
        self.reset_button.clicked.connect(self.reset_filters)
        filters_layout.addWidget(self.reset_button, 1, 5)
        filters_layout.setColumnStretch(0, 2)
        filters_layout.setColumnStretch(1, 1)
        filters_layout.setColumnStretch(2, 1)
        layout.addWidget(filters)

        self.student_filter.currentIndexChanged.connect(self._filters_changed_now)
        self.subject_filter.currentIndexChanged.connect(self._filters_changed_now)
        self.status_filter.currentIndexChanged.connect(self._filters_changed_now)
        self.period_enabled.toggled.connect(self._period_toggled)
        self.date_from.dateChanged.connect(self._filters_changed_now)
        self.date_to.dateChanged.connect(self._filters_changed_now)
        self.search.textChanged.connect(lambda _text: self.search_timer.start())

        self.loading_label = QLabel("Откройте вкладку, чтобы загрузить локальный архив")
        self.loading_label.setObjectName("muted")
        layout.addWidget(self.loading_label)

        splitter = QSplitter(Qt.Horizontal)
        list_panel = QWidget()
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Дата", "Ученик", "Предмет", "Тема", "Статус"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._load_selected)
        list_layout.addWidget(self.table, 1)
        paging = QHBoxLayout()
        self.previous_button = set_button_kind(QPushButton("← Назад"), "ghost")
        self.previous_button.clicked.connect(self.previous_page)
        paging.addWidget(self.previous_button)
        self.page_label = QLabel("Занятия не загружены")
        self.page_label.setObjectName("muted")
        self.page_label.setAlignment(Qt.AlignCenter)
        paging.addWidget(self.page_label, 1)
        self.next_button = set_button_kind(QPushButton("Вперёд →"), "ghost")
        self.next_button.clicked.connect(self.next_page)
        paging.addWidget(self.next_button)
        list_layout.addLayout(paging)
        splitter.addWidget(list_panel)

        details = QFrame()
        details.setObjectName("contentDetails")
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(18, 16, 18, 16)
        details_layout.setSpacing(10)
        details_header = QHBoxLayout()
        details_title = QLabel("Содержимое занятия")
        details_title.setObjectName("tileTitle")
        details_header.addWidget(details_title, 1)
        self.close_details_button = set_button_kind(QPushButton("Закрыть карточку"), "ghost")
        self.close_details_button.clicked.connect(self.close_details)
        details_header.addWidget(self.close_details_button)
        details_layout.addLayout(details_header)
        metadata_form = QFormLayout()
        metadata_form.setVerticalSpacing(6)
        self.metadata: dict[str, QLabel] = {}
        for key, label in (
            ("student", "Ученик"),
            ("date", "Дата"),
            ("subject", "Предмет"),
            ("topic", "Тема"),
            ("status", "Статус"),
            ("lesson_id", "ID занятия"),
            ("updated", "Обновлено"),
        ):
            value = QLabel("—")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.metadata[key] = value
            metadata_form.addRow(label, value)
        details_layout.addLayout(metadata_form)

        self.playback_panel = PlaybackPanel(
            self.playback_controller,
            self.playback_backend,
        )
        self.playback_panel.status_changed.connect(self.status_changed)
        details_layout.addWidget(self.playback_panel)

        files_header = QHBoxLayout()
        files_title = QLabel("Файлы")
        files_title.setObjectName("eyebrow")
        files_header.addWidget(files_title, 1)
        self.open_file_button = set_button_kind(QPushButton("Открыть файл"), "ghost")
        self.open_file_button.setEnabled(False)
        self.open_file_button.clicked.connect(self.open_selected_file)
        files_header.addWidget(self.open_file_button)
        details_layout.addLayout(files_header)
        self.files_table = QTableWidget(0, 4)
        self.files_table.setHorizontalHeaderLabels(["Тип", "Путь", "Размер", "Состояние"])
        self.files_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.files_table.setSelectionMode(QTableWidget.SingleSelection)
        self.files_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.files_table.setShowGrid(False)
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.files_table.itemSelectionChanged.connect(self._file_selection_changed)
        self.files_table.doubleClicked.connect(lambda _index: self.open_selected_file())
        details_layout.addWidget(self.files_table, 1)

        transcript_title = QLabel("Транскрипт")
        transcript_title.setObjectName("eyebrow")
        details_layout.addWidget(transcript_title)
        self.transcript_state = QLabel("Выберите занятие")
        self.transcript_state.setObjectName("muted")
        self.transcript_state.setWordWrap(True)
        details_layout.addWidget(self.transcript_state)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("Для занятия пока нет проиндексированного транскрипта")
        details_layout.addWidget(self.transcript, 2)
        splitter.addWidget(details)
        splitter.setSizes([670, 500])
        layout.addWidget(splitter, 1)
        self._clear_details()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.ensure_loaded()

    def hideEvent(self, event: QHideEvent) -> None:
        self.playback_panel.stop(clear_source=True)
        super().hideEvent(event)

    def set_students(self, students: list[Student]) -> None:
        selected = self.student_filter.currentData() if hasattr(self, "student_filter") else None
        selected_subject = self.subject_filter.currentData() if hasattr(self, "subject_filter") else None
        self.student_filter.blockSignals(True)
        self.student_filter.clear()
        self.student_filter.addItem("Все ученики", None)
        for student in sorted(students, key=lambda item: item.full_name.casefold()):
            self.student_filter.addItem(student.full_name, student.id)
        index = self.student_filter.findData(selected)
        self.student_filter.setCurrentIndex(max(0, index))
        self.student_filter.blockSignals(False)
        subjects = {"mathematics", "physics", "chemistry"}
        for student in students:
            subjects.update(student.subjects)
        self.subject_filter.blockSignals(True)
        self.subject_filter.clear()
        self.subject_filter.addItem("Все предметы", None)
        for subject in sorted(subjects, key=str.casefold):
            self.subject_filter.addItem(subject, subject)
        subject_index = self.subject_filter.findData(selected_subject)
        self.subject_filter.setCurrentIndex(max(0, subject_index))
        self.subject_filter.blockSignals(False)

    def ensure_loaded(self) -> None:
        if self._initial_sync_started:
            return
        self._initial_sync_started = True
        self.refresh()
        self.synchronize()

    def synchronize(self) -> None:
        if self._sync_running:
            return
        self._sync_running = True
        self.sync_button.setEnabled(False)
        self.loading_label.setText("Синхронизирую локальный каталог…")
        self.status_changed.emit("Синхронизирую материалы…", "working")
        self.run_background(
            self.service.index_existing_lessons,
            self._synchronization_ready,
            self._synchronization_failed,
        )

    def _synchronization_ready(self, result: object) -> None:
        self._sync_running = False
        self.sync_button.setEnabled(True)
        errors = getattr(result, "errors", [])
        if errors:
            self.loading_label.setText(f"Индекс обновлён с предупреждениями: {len(errors)}")
            self.status_changed.emit("Материалы обновлены с предупреждениями", "warning")
        else:
            self.loading_label.setText("Индекс локальных материалов обновлён")
            self.status_changed.emit("Материалы синхронизированы", "success")
        self.refresh()

    def _synchronization_failed(self, details: str) -> None:
        self._sync_running = False
        self.sync_button.setEnabled(True)
        self.loading_label.setText("Не удалось синхронизировать локальный каталог")
        self.status_changed.emit("Ошибка синхронизации материалов", "error")
        self.loading_label.setToolTip(details[-3000:])

    def _period_toggled(self, enabled: bool) -> None:
        self.date_from.setEnabled(enabled)
        self.date_to.setEnabled(enabled)
        self._filters_changed_now()

    def _filters_changed_now(self, *_args) -> None:
        self.offset = 0
        if self._initial_sync_started:
            self.refresh()

    def reset_filters(self) -> None:
        for combo in (self.student_filter, self.subject_filter, self.status_filter):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.period_enabled.blockSignals(True)
        self.period_enabled.setChecked(False)
        self.period_enabled.blockSignals(False)
        self.date_from.setEnabled(False)
        self.date_to.setEnabled(False)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.offset = 0
        self.refresh()

    def _filters(self) -> LessonFilters:
        status_value = self.status_filter.currentData()
        date_from = self._date_value(self.date_from) if self.period_enabled.isChecked() else None
        date_to = self._date_value(self.date_to) if self.period_enabled.isChecked() else None
        if date_from and date_to and date_from > date_to:
            date_from, date_to = date_to, date_from
        return LessonFilters(
            student_id=self.student_filter.currentData(),
            subject=self.subject_filter.currentData(),
            status=JobStatus(status_value) if status_value else None,
            query=self.search.text(),
            lesson_date_from=date_from,
            lesson_date_to=date_to,
            limit=self.page_size,
            offset=self.offset,
        )

    @staticmethod
    def _date_value(widget: QDateEdit) -> date:
        value = widget.date()
        return date(value.year(), value.month(), value.day())

    def refresh(self) -> None:
        self._list_request += 1
        request_id = self._list_request
        filters = self._filters()
        self.refresh_button.setEnabled(False)
        self.loading_label.setText("Загружаю занятия из SQLite…")
        self.run_background(
            lambda: self.service.list_lessons(filters),
            lambda result, current=request_id: self._list_ready(current, result),
            lambda details, current=request_id: self._list_failed(current, details),
        )

    def _list_ready(self, request_id: int, result: object) -> None:
        if request_id != self._list_request:
            return
        page = result
        if not isinstance(page, LessonPage):
            self._list_failed(request_id, "Некорректный результат загрузки")
            return
        self.refresh_button.setEnabled(True)
        self.total = page.total
        self.loading_label.setText("Список загружен из локальной базы")
        self.page_label.setText(pagination_text(page))
        self.previous_button.setEnabled(page.offset > 0)
        self.next_button.setEnabled(page.offset + len(page.items) < page.total)
        selected = self._selected_lesson_id
        self.table.blockSignals(True)
        self.table.setRowCount(len(page.items))
        selected_row = -1
        for row, lesson in enumerate(page.items):
            values = (
                lesson.lesson_date.strftime("%d.%m.%Y"),
                lesson.student.full_name,
                lesson.subject,
                lesson.topic,
                status_label(lesson.status),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, lesson.lesson_id)
                self.table.setItem(row, column, item)
            if lesson.lesson_id == selected:
                selected_row = row
        self.table.blockSignals(False)
        if selected_row < 0 and page.items:
            selected_row = 0
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        else:
            self._selected_lesson_id = None
            self._clear_details()

    def _list_failed(self, request_id: int, details: str) -> None:
        if request_id != self._list_request:
            return
        self.refresh_button.setEnabled(True)
        self.loading_label.setText("Не удалось загрузить список занятий")
        self.loading_label.setToolTip(details[-3000:])
        self.status_changed.emit("Ошибка загрузки материалов", "error")

    def previous_page(self) -> None:
        self.offset = max(0, self.offset - self.page_size)
        self.refresh()

    def next_page(self) -> None:
        if self.offset + self.page_size < self.total:
            self.offset += self.page_size
            self.refresh()

    def show_student(self, student_id: str) -> None:
        index = self.student_filter.findData(student_id)
        if index < 0:
            return
        self.student_filter.blockSignals(True)
        self.student_filter.setCurrentIndex(index)
        self.student_filter.blockSignals(False)
        self.offset = 0
        if self._initial_sync_started:
            self.refresh()
        else:
            self.ensure_loaded()

    def _load_selected(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        lesson_id = str(items[0].data(Qt.UserRole))
        if not lesson_id:
            return
        if lesson_id != self._selected_lesson_id:
            self.playback_panel.stop(clear_source=True)
        self._selected_lesson_id = lesson_id
        self._detail_request += 1
        request_id = self._detail_request
        self.transcript_state.setText("Загружаю содержимое занятия…")
        self.run_background(
            lambda: self.service.get_lesson(lesson_id),
            lambda result, current=request_id: self._detail_ready(current, result),
            lambda details, current=request_id: self._detail_failed(current, details),
        )

    def _detail_ready(self, request_id: int, result: object) -> None:
        if request_id != self._detail_request or not isinstance(result, LessonContent):
            return
        lesson = result.lesson
        self.metadata["student"].setText(lesson.student.full_name)
        self.metadata["date"].setText(lesson.lesson_date.strftime("%d.%m.%Y"))
        self.metadata["subject"].setText(lesson.subject)
        self.metadata["topic"].setText(lesson.topic)
        self.metadata["status"].setText(status_label(lesson.status))
        self.metadata["lesson_id"].setText(lesson.lesson_id)
        self.metadata["updated"].setText(lesson.updated_at.astimezone().strftime("%d.%m.%Y %H:%M"))

        rows = content_file_rows(result, self.workspace)
        audio_paths = [
            file.absolute_path
            for file in rows
            if file.absolute_path and file.exists and is_audio_path(file.absolute_path)
        ]
        self.playback_panel.set_tracks(audio_paths)
        segment_result = None
        if lesson.artifacts.segments_json:
            segment_path, _display, state = resolve_known_path(
                lesson.artifacts.segments_json,
                self.workspace,
            )
            if segment_path and state == "available":
                segment_result = load_playback_segments(segment_path)
            elif state == "outside_workspace":
                segment_result = SegmentLoadResult(error="Файл сегментов находится вне каталога данных")
            else:
                segment_result = SegmentLoadResult(error="Файл сегментов отсутствует")
        self.playback_panel.set_segments(
            segment_result.segments if segment_result else (),
            segment_result.error if segment_result else None,
        )
        self.files_table.blockSignals(True)
        self.files_table.setRowCount(len(rows))
        for row_index, file in enumerate(rows):
            values = (
                KIND_LABELS.get(file.kind, file.kind),
                file.display_path,
                format_size(file.size_bytes) if file.size_bytes else "—",
                file.state_label,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if file.absolute_path and file.exists:
                    item.setData(Qt.UserRole, str(file.absolute_path))
                if file.state == "missing":
                    item.setForeground(QBrush(QColor("#A33636")))
                elif file.state == "outside_workspace":
                    item.setForeground(QBrush(QColor("#A15C00")))
                self.files_table.setItem(row_index, column, item)
        self.files_table.blockSignals(False)
        self.open_file_button.setEnabled(False)

        if result.transcript:
            self.transcript.setPlainText(result.transcript.content)
            transcript_path = self.workspace / result.transcript.relative_path
            state = f"Версия {result.transcript.revision_number} · {result.transcript.created_by}"
            if not transcript_path.is_file():
                state += " · исходный файл отсутствует, показана сохранённая копия SQLite"
                self.transcript_state.setStyleSheet("color: #A15C00;")
            else:
                self.transcript_state.setStyleSheet("")
            self.transcript_state.setText(state)
        else:
            self.transcript.clear()
            self.transcript_state.setStyleSheet("")
            self.transcript_state.setText("Для занятия нет проиндексированного транскрипта")

    def _detail_failed(self, request_id: int, details: str) -> None:
        if request_id != self._detail_request:
            return
        self._clear_details()
        self.transcript_state.setText("Не удалось загрузить содержимое занятия")
        self.transcript_state.setToolTip(details[-3000:])

    def _clear_details(self) -> None:
        for label in getattr(self, "metadata", {}).values():
            label.setText("—")
        if hasattr(self, "files_table"):
            self.files_table.setRowCount(0)
        if hasattr(self, "transcript"):
            self.transcript.clear()
        if hasattr(self, "transcript_state"):
            self.transcript_state.setText("Выберите занятие")
            self.transcript_state.setStyleSheet("")
        if hasattr(self, "open_file_button"):
            self.open_file_button.setEnabled(False)
            self.open_file_button.setText("Открыть файл")
        if hasattr(self, "playback_panel"):
            self.playback_panel.reset()

    def close_details(self) -> None:
        self.playback_panel.stop(clear_source=True)
        self.table.clearSelection()
        self._selected_lesson_id = None
        self._detail_request += 1
        self._clear_details()

    def _file_selection_changed(self) -> None:
        items = self.files_table.selectedItems()
        path = items[0].data(Qt.UserRole) if items else None
        self.open_file_button.setEnabled(bool(path))
        self.open_file_button.setText(
            "Воспроизвести" if path and is_audio_path(Path(str(path))) else "Открыть файл"
        )

    def open_selected_file(self) -> None:
        items = self.files_table.selectedItems()
        if not items:
            return
        value = items[0].data(Qt.UserRole)
        if not value:
            return
        path = Path(str(value))
        if path.is_file():
            if is_audio_path(path):
                self.playback_panel.play_path(path)
            else:
                self.file_open_requested.emit(path)
