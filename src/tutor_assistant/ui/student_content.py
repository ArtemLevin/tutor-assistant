from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TypeAlias

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QCloseEvent, QColor, QHideEvent, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..content import (
    ContentIntegrityReport,
    ContentMaintenanceResult,
    ContentOperation,
    DatabaseBackupInfo,
    DatabaseBackupVerification,
    DatabaseRestoreResult,
    ImportCancellationToken,
    LessonContent,
    LessonFilters,
    LessonImportRequest,
    LessonImportResult,
    LessonPage,
    StudentContentService,
    TranscriptDraft,
    TranscriptRevision,
    TrashActionResult,
    TrashSummary,
)
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
from .content_edit import (
    LessonMetadataEdit,
    MetadataEditDialog,
    RevisionHistoryDialog,
)
from .content_health import ContentHealthDialog
from .content_import import ImportLessonDialog
from .content_trash import ContentTrashDialog
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


class LessonContentDialog(QDialog):
    close_blocked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._close_allowed = True
        self.setWindowTitle("Содержимое занятия")
        self.setAccessibleName("Содержимое занятия")
        self.setWindowModality(Qt.WindowModal)
        self.setMinimumSize(760, 600)
        self.resize(920, 760)

    def set_close_allowed(self, allowed: bool) -> None:
        self._close_allowed = allowed

    def reject(self) -> None:
        if not self._close_allowed:
            self.close_blocked.emit()
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._close_allowed:
            event.ignore()
            self.close_blocked.emit()
            return
        super().closeEvent(event)


class StudentContentPage(QWidget):
    status_changed = Signal(str, str)
    file_open_requested = Signal(object)
    audio_queue_requested = Signal(object, object)
    lesson_purged = Signal(str)
    lesson_trashed = Signal(str)
    trash_retention_changed = Signal(int)

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
        self.students: list[Student] = []
        self.import_dialog: ImportLessonDialog | None = None
        self.import_cancellation: ImportCancellationToken | None = None
        self.metadata_dialog: MetadataEditDialog | None = None
        self.history_dialog: RevisionHistoryDialog | None = None
        self.trash_dialog: ContentTrashDialog | None = None
        self.health_dialog: ContentHealthDialog | None = None
        self._current_content: LessonContent | None = None
        self._transcript_editing = False
        self._transcript_base_revision: int | None = None
        self._draft_running = False
        self._draft_saving_text = ""
        self._save_after_draft = False
        self._cancel_after_draft = False
        self._build()
        self._install_shortcuts()
        self.draft_timer = QTimer(self)
        self.draft_timer.setSingleShot(True)
        self.draft_timer.setInterval(900)
        self.draft_timer.timeout.connect(self._save_transcript_draft)
        self.transcript.textChanged.connect(self._schedule_transcript_draft)
        self.set_students(students)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self._filters_changed_now)

    def _build(self) -> None:
        self.setAccessibleName("Архив материалов учеников")
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
        self.import_button = set_button_kind(QPushButton("Создать / импортировать"), "ghost")
        self.import_button.setToolTip("Создать карточку занятия и безопасно скопировать аудио или транскрипт")
        self.import_button.clicked.connect(self.open_import_dialog)
        heading.addWidget(self.import_button)
        self.trash_button = set_button_kind(QPushButton("Корзина"), "ghost")
        self.trash_button.setToolTip("Открыть удалённые занятия · Ctrl+Shift+Delete")
        self.trash_button.clicked.connect(self.open_trash)
        heading.addWidget(self.trash_button)
        self.health_button = set_button_kind(QPushButton("Диагностика"), "ghost")
        self.health_button.setToolTip("Проверить индекс, файлы и место · Ctrl+Shift+D")
        self.health_button.clicked.connect(self.open_content_health)
        heading.addWidget(self.health_button)
        self.sync_button = set_button_kind(QPushButton("Проверить и восстановить"), "ghost")
        self.sync_button.setToolTip(
            "Восстановить файлы и индекс архива; карточки SQLite не перезаписываются с диска"
        )
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
        self.search.setPlaceholderText("Тема, ученик, предмет или текст транскрипта")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Полнотекстовый поиск по материалам")
        self.search.setAccessibleDescription(
            "Поиск по карточкам занятий и последним подтверждённым транскриптам"
        )
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
        self.table.setAccessibleName("Список занятий")
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
        layout.addWidget(list_panel, 1)

        self.details_dialog = LessonContentDialog(self)
        self.details_dialog.close_blocked.connect(
            lambda: self.status_changed.emit(
                "Завершите редактирование транскрипта перед закрытием карточки",
                "warning",
            )
        )
        self.details_dialog.finished.connect(self._details_dialog_closed)
        dialog_layout = QVBoxLayout(self.details_dialog)
        dialog_layout.setContentsMargins(12, 12, 12, 12)
        details = QFrame()
        details.setObjectName("contentDetails")
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(18, 16, 18, 16)
        details_layout.setSpacing(10)
        details_header = QHBoxLayout()
        details_title = QLabel("Содержимое занятия")
        details_title.setObjectName("tileTitle")
        details_header.addWidget(details_title, 1)
        self.edit_metadata_button = set_button_kind(QPushButton("Изменить карточку"), "ghost")
        self.edit_metadata_button.setEnabled(False)
        self.edit_metadata_button.clicked.connect(self.open_metadata_editor)
        details_header.addWidget(self.edit_metadata_button)
        self.delete_lesson_button = set_button_kind(QPushButton("В корзину"), "danger")
        self.delete_lesson_button.setEnabled(False)
        self.delete_lesson_button.clicked.connect(self.delete_selected_lesson)
        details_header.addWidget(self.delete_lesson_button)
        self.close_details_button = set_button_kind(QPushButton("Закрыть карточку"), "ghost")
        self.close_details_button.clicked.connect(self.details_dialog.close)
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
            ("materials", "Материалы"),
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
        self.files_table.setAccessibleName("Файлы выбранного занятия")
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.files_table.itemSelectionChanged.connect(self._file_selection_changed)
        self.files_table.doubleClicked.connect(lambda _index: self.open_selected_file())
        details_layout.addWidget(self.files_table, 1)

        transcript_header = QHBoxLayout()
        transcript_title = QLabel("Транскрипт")
        transcript_title.setObjectName("eyebrow")
        transcript_header.addWidget(transcript_title, 1)
        self.history_button = set_button_kind(QPushButton("История"), "ghost")
        self.history_button.setEnabled(False)
        self.history_button.clicked.connect(self.open_revision_history)
        transcript_header.addWidget(self.history_button)
        self.edit_transcript_button = set_button_kind(QPushButton("Редактировать"), "ghost")
        self.edit_transcript_button.setEnabled(False)
        self.edit_transcript_button.clicked.connect(self.start_transcript_editing)
        transcript_header.addWidget(self.edit_transcript_button)
        details_layout.addLayout(transcript_header)
        self.transcript_state = QLabel("Выберите занятие")
        self.transcript_state.setObjectName("muted")
        self.transcript_state.setWordWrap(True)
        details_layout.addWidget(self.transcript_state)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setAccessibleName("Подтверждённый транскрипт занятия")
        self.transcript.setPlaceholderText("Для занятия пока нет проиндексированного транскрипта")
        details_layout.addWidget(self.transcript, 2)
        self.transcript_actions = QWidget()
        transcript_actions_layout = QHBoxLayout(self.transcript_actions)
        transcript_actions_layout.setContentsMargins(0, 0, 0, 0)
        transcript_actions_layout.addStretch(1)
        self.cancel_transcript_button = set_button_kind(QPushButton("Закрыть редактор"), "ghost")
        self.cancel_transcript_button.clicked.connect(self.cancel_transcript_editing)
        transcript_actions_layout.addWidget(self.cancel_transcript_button)
        self.save_transcript_button = set_button_kind(QPushButton("Сохранить версию"), "primary")
        self.save_transcript_button.clicked.connect(self.save_transcript_editing)
        transcript_actions_layout.addWidget(self.save_transcript_button)
        self.transcript_actions.setVisible(False)
        details_layout.addWidget(self.transcript_actions)
        dialog_layout.addWidget(details)
        self._clear_details()

    def _install_shortcuts(self) -> None:
        self.search_shortcut = QShortcut(QKeySequence.Find, self)
        self.search_shortcut.activated.connect(self._focus_search)
        self.refresh_shortcut = QShortcut(QKeySequence.Refresh, self)
        self.refresh_shortcut.activated.connect(self.refresh)
        self.sync_shortcut = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        self.sync_shortcut.activated.connect(self.synchronize)
        self.import_shortcut = QShortcut(QKeySequence.New, self)
        self.import_shortcut.activated.connect(self.open_import_dialog)
        self.trash_shortcut = QShortcut(QKeySequence("Ctrl+Shift+Delete"), self)
        self.trash_shortcut.activated.connect(self.open_trash)
        self.health_shortcut = QShortcut(QKeySequence("Ctrl+Shift+D"), self)
        self.health_shortcut.activated.connect(self.open_content_health)
        self.delete_shortcut = QShortcut(QKeySequence.Delete, self.table)
        self.delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.delete_shortcut.activated.connect(self.delete_selected_lesson)
        self.save_shortcut = QShortcut(QKeySequence.Save, self.transcript)
        self.save_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.save_shortcut.activated.connect(self._save_transcript_shortcut)

    def _focus_search(self) -> None:
        window = self.window()
        window.raise_()
        window.activateWindow()
        QTimer.singleShot(0, self._apply_search_focus)

    def _apply_search_focus(self) -> None:
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def _save_transcript_shortcut(self) -> None:
        if self._transcript_editing:
            self.save_transcript_editing()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._initial_sync_started:
            self.refresh()
        else:
            self.ensure_loaded()

    def hideEvent(self, event: QHideEvent) -> None:
        if self._transcript_editing:
            self.draft_timer.stop()
            if not self._draft_running:
                self._save_transcript_draft()
        self.playback_panel.stop(clear_source=True)
        super().hideEvent(event)

    def set_students(self, students: list[Student]) -> None:
        self.students = list(students)
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

    def open_import_dialog(self) -> None:
        if self.import_dialog is not None:
            self.import_dialog.raise_()
            self.import_dialog.activateWindow()
            return
        dialog = ImportLessonDialog(self.students, self)
        self.import_dialog = dialog
        dialog.import_requested.connect(lambda request, current=dialog: self._start_import(current, request))
        dialog.cancellation_requested.connect(self._cancel_import)
        dialog.finished.connect(lambda _result, current=dialog: self._import_dialog_finished(current))
        dialog.open()

    def _start_import(
        self,
        dialog: ImportLessonDialog,
        request: LessonImportRequest,
    ) -> None:
        if self.import_cancellation is not None:
            return
        token = ImportCancellationToken()
        self.import_cancellation = token
        self.import_button.setEnabled(False)
        dialog.set_running()
        self.status_changed.emit("Импортирую занятие…", "working")
        self.run_background(
            lambda: self.service.import_lesson(
                request,
                cancellation=token,
                progress=dialog.progress_changed.emit,
            ),
            lambda result, current=dialog: self._import_ready(current, result),
            lambda details, current=dialog: self._import_failed(current, details),
        )

    def _cancel_import(self) -> None:
        if self.import_cancellation:
            self.import_cancellation.cancel()

    def _import_ready(self, dialog: ImportLessonDialog, result: object) -> None:
        self.import_cancellation = None
        self.import_button.setEnabled(True)
        if not isinstance(result, LessonImportResult):
            self._import_failed(dialog, "Некорректный результат импорта")
            return
        if result.cancelled:
            dialog.finish_cancelled()
            self.status_changed.emit("Импорт отменён, временные данные удалены", "warning")
            return
        if result.lesson is None:
            self._import_failed(dialog, "Импорт не вернул созданное занятие")
            return
        self._selected_lesson_id = result.lesson.lesson_id
        dialog.finish_success()
        self.status_changed.emit(
            f"Занятие импортировано · {result.lesson.student.full_name}",
            "success",
        )
        self.refresh()
        if result.enqueue_audio and result.audio_path:
            self.audio_queue_requested.emit(result.lesson, result.audio_path)

    def _import_failed(self, dialog: ImportLessonDialog, details: str) -> None:
        self.import_cancellation = None
        self.import_button.setEnabled(True)
        lines = [line.strip() for line in details.splitlines() if line.strip()]
        message = lines[-1] if lines else "Не удалось импортировать занятие"
        if ": " in message:
            message = message.split(": ", 1)[1]
        dialog.show_error(message)
        self.status_changed.emit("Ошибка импорта занятия", "error")

    def _import_dialog_finished(self, dialog: ImportLessonDialog) -> None:
        if self.import_dialog is dialog:
            self.import_dialog = None

    def delete_selected_lesson(self) -> None:
        content = self._current_content
        if content is None:
            return
        lesson = content.lesson
        if lesson.status in {JobStatus.RECORDING, JobStatus.TRANSCRIBING}:
            message = "Нельзя удалить занятие во время записи или транскрибации"
            self.status_changed.emit(message, "warning")
            QMessageBox.warning(self, "Удаление недоступно", message)
            return
        answer = QMessageBox.question(
            self,
            "Переместить занятие в корзину",
            f"Переместить «{lesson.topic}» в локальную корзину? Занятие можно будет восстановить.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self.delete_lesson_button.setEnabled(False)
        self.status_changed.emit("Перемещаю занятие в корзину…", "working")
        self.run_background(
            lambda: self.service.delete_lesson(lesson.lesson_id),
            self._lesson_deleted,
            self._lesson_delete_failed,
        )

    def _lesson_deleted(self, result: object) -> None:
        if not isinstance(result, TrashActionResult):
            self._lesson_delete_failed("Некорректный результат удаления")
            return
        self.close_details()
        self.lesson_trashed.emit(result.lesson_id)
        self.status_changed.emit("Занятие перемещено в корзину", "success")
        self.refresh()

    def _lesson_delete_failed(self, details: str) -> None:
        self.delete_lesson_button.setEnabled(self._current_content is not None)
        message = self._operation_message(details, "Не удалось переместить занятие в корзину")
        expected = message.startswith("Нельзя удалить")
        self.status_changed.emit(message, "warning" if expected else "error")
        QMessageBox.warning(
            self,
            "Удаление недоступно" if expected else "Корзина",
            message,
        )

    def open_trash(self) -> None:
        if self.trash_dialog is not None:
            self.trash_dialog.raise_()
            self.trash_dialog.activateWindow()
            return
        dialog = ContentTrashDialog(self.service.trash_retention_days, self)
        self.trash_dialog = dialog
        dialog.restore_requested.connect(
            lambda lesson_id, current=dialog: self._restore_from_trash(current, lesson_id)
        )
        dialog.purge_requested.connect(
            lambda lesson_id, current=dialog: self._purge_from_trash(current, lesson_id)
        )
        dialog.purge_expired_requested.connect(lambda current=dialog: self._purge_expired(current))
        dialog.retention_changed.connect(self._change_trash_retention)
        dialog.refresh_requested.connect(lambda current=dialog: self._reload_trash(current))
        dialog.finished.connect(lambda _result, current=dialog: self._trash_dialog_closed(current))
        dialog.open()
        self._reload_trash(dialog)

    def _reload_trash(self, dialog: ContentTrashDialog) -> None:
        dialog.set_busy("Загружаю корзину и журнал…")
        self.run_background(
            lambda: (self.service.trash_summary(), self.service.repository.list_operations()),
            lambda result, current=dialog: self._trash_ready(current, result),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось загрузить корзину")
            ),
        )

    def _trash_ready(self, dialog: ContentTrashDialog, result: object) -> None:
        if (
            not isinstance(result, tuple)
            or len(result) != 2
            or not isinstance(result[0], TrashSummary)
            or not isinstance(result[1], list)
            or not all(isinstance(item, ContentOperation) for item in result[1])
        ):
            dialog.show_error("Некорректный результат загрузки корзины")
            return
        dialog.table.setEnabled(True)
        dialog.set_data(result[0], result[1])

    def _restore_from_trash(self, dialog: ContentTrashDialog, lesson_id: str) -> None:
        self.run_background(
            lambda: self.service.restore_lesson(lesson_id),
            lambda result, current=dialog: self._trash_action_ready(current, result, "Занятие восстановлено"),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось восстановить занятие")
            ),
        )

    def _purge_from_trash(self, dialog: ContentTrashDialog, lesson_id: str) -> None:
        self.run_background(
            lambda: self.service.permanently_delete_lesson(lesson_id),
            lambda result, current=dialog: self._trash_action_ready(
                current, result, "Локальные данные удалены навсегда"
            ),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось удалить занятие")
            ),
        )

    def _purge_expired(self, dialog: ContentTrashDialog) -> None:
        self.run_background(
            self.service.purge_expired_trash,
            lambda result, current=dialog: self._expired_purge_ready(current, result),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось очистить просроченные занятия")
            ),
        )

    def _trash_action_ready(
        self,
        dialog: ContentTrashDialog,
        result: object,
        message: str,
    ) -> None:
        if not isinstance(result, TrashActionResult):
            dialog.show_error("Некорректный результат операции")
            return
        if result.operation.value == "purge":
            self.lesson_purged.emit(result.lesson_id)
            message += f" · освобождено {format_size(result.size_bytes)}"
        self.status_changed.emit(message, "success")
        self.refresh()
        self._reload_trash(dialog)

    def _expired_purge_ready(self, dialog: ContentTrashDialog, result: object) -> None:
        if not isinstance(result, list) or not all(isinstance(item, TrashActionResult) for item in result):
            dialog.show_error("Некорректный результат очистки")
            return
        for item in result:
            self.lesson_purged.emit(item.lesson_id)
        released = sum(item.size_bytes for item in result)
        self.status_changed.emit(
            f"Корзина очищена · освобождено {format_size(released)}",
            "success",
        )
        self._reload_trash(dialog)

    def _change_trash_retention(self, days: int) -> None:
        try:
            self.service.set_trash_retention_days(days)
        except ValueError as exc:
            if self.trash_dialog:
                self.trash_dialog.show_error(str(exc))
            return
        self.trash_retention_changed.emit(days)
        self.status_changed.emit(f"Срок хранения корзины: {days} дн.", "success")
        if self.trash_dialog:
            self._reload_trash(self.trash_dialog)

    def _trash_dialog_closed(self, dialog: ContentTrashDialog) -> None:
        if self.trash_dialog is dialog:
            self.trash_dialog = None

    def open_content_health(self) -> None:
        if self.health_dialog is not None:
            self.health_dialog.raise_()
            self.health_dialog.activateWindow()
            return
        dialog = ContentHealthDialog(self)
        self.health_dialog = dialog
        dialog.rescan_requested.connect(lambda current=dialog: self._reload_content_health(current))
        dialog.repair_requested.connect(lambda current=dialog: self._repair_content_health(current))
        dialog.cleanup_requested.connect(lambda current=dialog: self._cleanup_content_temp(current))
        dialog.rebuild_search_requested.connect(lambda current=dialog: self._rebuild_content_search(current))
        dialog.backup_requested.connect(lambda current=dialog: self._create_database_backup(current))
        dialog.verify_backup_requested.connect(lambda current=dialog: self._verify_database_backup(current))
        dialog.restore_backup_requested.connect(lambda current=dialog: self._restore_database_backup(current))
        dialog.finished.connect(lambda _result, current=dialog: self._health_dialog_closed(current))
        dialog.open()
        self._reload_content_health(dialog)

    def _reload_content_health(self, dialog: ContentHealthDialog) -> None:
        dialog.set_busy("Проверяю SQLite, индекс и локальные файлы…")
        self.run_background(
            self.service.inspect_content_integrity,
            lambda result, current=dialog: self._content_health_ready(current, result),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось проверить хранилище")
            ),
        )

    def _content_health_ready(self, dialog: ContentHealthDialog, result: object) -> None:
        if not isinstance(result, ContentIntegrityReport):
            dialog.show_error("Некорректный результат диагностики")
            return
        dialog.set_report(result)
        tone = "success" if result.healthy else "warning"
        self.status_changed.emit(
            f"Диагностика: ошибок {result.errors}, предупреждений {result.warnings}",
            tone,
        )

    def _cleanup_content_temp(self, dialog: ContentHealthDialog) -> None:
        self.run_background(
            lambda: self.service.run_maintenance(
                auto_repair=False,
                purge_expired=False,
                cleanup_temporary=True,
            ),
            lambda result, current=dialog: self._content_cleanup_ready(current, result),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось очистить временные данные")
            ),
        )

    def _content_cleanup_ready(self, dialog: ContentHealthDialog, result: object) -> None:
        if not isinstance(result, ContentMaintenanceResult):
            dialog.show_error("Некорректный результат очистки")
            return
        cleanup = result.temporary_cleanup
        tone = "warning" if result.errors else "success"
        self.status_changed.emit(
            f"Временные данные очищены · освобождено {format_size(cleanup.released_bytes)} · "
            f"ошибок: {len(result.errors)}",
            tone,
        )
        self._reload_content_health(dialog)

    def _repair_content_health(self, dialog: ContentHealthDialog) -> None:
        dialog.set_busy("Восстанавливаю файловую и поисковую проекции из SQLite…")
        self.run_background(
            self.service.repair_content_integrity,
            lambda result, current=dialog: self._content_repair_ready(current, result),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось восстановить архив")
            ),
        )

    def _content_repair_ready(self, dialog: ContentHealthDialog, result: object) -> None:
        if not isinstance(result, ContentMaintenanceResult):
            dialog.show_error("Некорректный результат восстановления")
            return
        tone = "warning" if result.errors else "success"
        self.status_changed.emit(
            f"Архив восстановлен · занятий {len(result.repaired_lessons)} · ошибок {len(result.errors)}",
            tone,
        )
        self.refresh()
        self._reload_content_health(dialog)

    def _rebuild_content_search(self, dialog: ContentHealthDialog) -> None:
        dialog.set_busy("Перестраиваю полнотекстовый индекс…")
        self.run_background(
            self.service.coordinated_rebuild_search_index,
            lambda result, current=dialog: self._content_search_rebuilt(current, result),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось перестроить поиск")
            ),
        )

    def _content_search_rebuilt(self, dialog: ContentHealthDialog, result: object) -> None:
        if not isinstance(result, int):
            dialog.show_error("Некорректный результат перестроения индекса")
            return
        self.status_changed.emit(f"Полнотекстовый индекс обновлён: {result}", "success")
        self.refresh()
        self._reload_content_health(dialog)

    def _health_dialog_closed(self, dialog: ContentHealthDialog) -> None:
        if self.health_dialog is dialog:
            self.health_dialog = None

    def _backup_file(self, title: str) -> Path | None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            title,
            str((self.workspace / "backups").resolve()),
            "SQLite backup (*.sqlite3)",
        )
        return Path(path) if path else None

    def _create_database_backup(self, dialog: ContentHealthDialog) -> None:
        dialog.set_busy("Создаю согласованную резервную копию SQLite…")
        self.run_background(
            lambda: self.service.create_database_backup(reason="manual-gui"),
            lambda result, current=dialog: self._database_backup_ready(current, result),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось создать резервную копию")
            ),
        )

    def _database_backup_ready(self, dialog: ContentHealthDialog, result: object) -> None:
        if not isinstance(result, DatabaseBackupInfo):
            dialog.show_error("Некорректный результат резервного копирования")
            return
        dialog.show_result(f"Backup создан: {result.path.name} · {format_size(result.manifest.size_bytes)}")
        self.status_changed.emit("Резервная копия SQLite создана", "success")

    def _verify_database_backup(self, dialog: ContentHealthDialog) -> None:
        path = self._backup_file("Проверить резервную копию")
        if path is None:
            return
        dialog.set_busy("Проверяю manifest, SHA-256 и целостность SQLite…")
        self.run_background(
            lambda: self.service.verify_database_backup(path),
            lambda result, current=dialog: self._database_backup_verified(current, result),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось проверить резервную копию")
            ),
        )

    def _database_backup_verified(self, dialog: ContentHealthDialog, result: object) -> None:
        if not isinstance(result, DatabaseBackupVerification):
            dialog.show_error("Некорректный результат проверки backup")
        elif result.valid:
            dialog.show_result(f"Backup проверен: {result.path.name}")
        else:
            dialog.show_error("Backup повреждён: " + "; ".join(result.errors))

    def _restore_database_backup(self, dialog: ContentHealthDialog) -> None:
        path = self._backup_file("Восстановить резервную копию")
        if path is None:
            return
        answer = QMessageBox.warning(
            self,
            "Восстановить SQLite",
            "Текущая база данных будет заменена содержимым backup. Перед заменой приложение "
            "автоматически создаст safety-копию. Продолжить?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        dialog.set_busy("Проверяю backup и восстанавливаю SQLite…")
        self.run_background(
            lambda: self.service.restore_database_backup(path),
            lambda result, current=dialog: self._database_restore_ready(current, result),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось восстановить SQLite")
            ),
        )

    def _database_restore_ready(self, dialog: ContentHealthDialog, result: object) -> None:
        if not isinstance(result, DatabaseRestoreResult):
            dialog.show_error("Некорректный результат восстановления")
            return
        self.refresh()
        safety_name = (
            result.safety_backup.path.name
            if result.safety_backup is not None
            else str(result.raw_safety_path or "—")
        )
        dialog.show_result(f"SQLite восстановлена. Safety backup: {safety_name}")
        self.status_changed.emit("SQLite восстановлена из резервной копии", "success")
        self._reload_content_health(dialog)

    @staticmethod
    def _operation_message(details: str, fallback: str) -> str:
        lines = [line.strip() for line in details.splitlines() if line.strip()]
        message = lines[-1] if lines else fallback
        return message.split(": ", 1)[-1] if ": " in message else message

    def open_metadata_editor(self) -> None:
        if self._current_content is None or self.metadata_dialog is not None:
            return
        dialog = MetadataEditDialog(
            self._current_content.lesson,
            self.students,
            self._current_content.row_version,
            self,
        )
        self.metadata_dialog = dialog
        dialog.save_requested.connect(lambda edit, current=dialog: self._save_metadata(current, edit))
        dialog.finished.connect(lambda _result, current=dialog: self._metadata_dialog_closed(current))
        dialog.open()

    def _save_metadata(self, dialog: MetadataEditDialog, edit: object) -> None:
        if not isinstance(edit, LessonMetadataEdit):
            dialog.show_error("Некорректные данные карточки")
            return
        self.run_background(
            lambda: self.service.update_lesson_metadata(
                edit.lesson_id,
                student=edit.student,
                subject=edit.subject,
                lesson_date=edit.lesson_date,
                topic=edit.topic,
                expected_updated_at=edit.expected_updated_at,
                expected_row_version=edit.expected_row_version,
            ),
            lambda _result, current=dialog: self._metadata_saved(current),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось сохранить карточку")
            ),
        )

    def _metadata_saved(self, dialog: MetadataEditDialog) -> None:
        dialog.accept()
        self.status_changed.emit("Карточка занятия обновлена", "success")
        self.refresh()

    def _metadata_dialog_closed(self, dialog: MetadataEditDialog) -> None:
        if self.metadata_dialog is dialog:
            self.metadata_dialog = None

    def start_transcript_editing(self) -> None:
        content = self._current_content
        if content is None or self._transcript_editing:
            return
        current_number = content.transcript.revision_number if content.transcript else None
        text = content.transcript.content if content.transcript else ""
        base_revision = current_number
        draft = content.draft
        if isinstance(draft, TranscriptDraft):
            text = draft.content
            base_revision = draft.base_revision_number
            if base_revision == current_number:
                self.transcript_state.setText("Восстановлен автосохранённый черновик")
            else:
                self.transcript_state.setText(
                    "Восстановлен черновик от другой версии; сохранение проверит конфликт"
                )
                self.transcript_state.setStyleSheet("color: #A15C00;")
        self._transcript_base_revision = base_revision
        self._transcript_editing = True
        self.table.setEnabled(False)
        self.edit_metadata_button.setEnabled(False)
        self.edit_transcript_button.setEnabled(False)
        self.history_button.setEnabled(False)
        self.delete_lesson_button.setEnabled(False)
        self.close_details_button.setEnabled(False)
        self.details_dialog.set_close_allowed(False)
        self.transcript.setReadOnly(False)
        self.transcript.blockSignals(True)
        self.transcript.setPlainText(text)
        self.transcript.blockSignals(False)
        self.transcript_actions.setVisible(True)
        self.transcript.setFocus()

    def _schedule_transcript_draft(self) -> None:
        if self._transcript_editing and not self._save_after_draft:
            self.transcript_state.setStyleSheet("")
            self.transcript_state.setText("Есть несохранённые изменения…")
            self.draft_timer.start()

    def _save_transcript_draft(self) -> None:
        if not self._transcript_editing:
            return
        if self._draft_running:
            return
        lesson_id = self._selected_lesson_id
        if not lesson_id:
            return
        text = self.transcript.toPlainText()
        self._draft_running = True
        self._draft_saving_text = text
        self.run_background(
            lambda: self.service.save_transcript_draft(
                lesson_id,
                text,
                base_revision_number=self._transcript_base_revision,
            ),
            self._transcript_draft_saved,
            self._transcript_draft_failed,
        )

    def _transcript_draft_saved(self, result: object) -> None:
        self._draft_running = False
        if not isinstance(result, TranscriptDraft):
            self._transcript_draft_failed("Некорректный результат автосохранения")
            return
        current_text = self.transcript.toPlainText()
        if self._transcript_editing and current_text != self._draft_saving_text:
            self._save_transcript_draft()
            return
        self.transcript_state.setStyleSheet("")
        self.transcript_state.setText(
            f"Черновик сохранён · {result.updated_at.astimezone().strftime('%H:%M:%S')}"
        )
        if self._save_after_draft:
            self._commit_transcript_save()
        elif self._cancel_after_draft:
            self._finish_transcript_editing()
            self.status_changed.emit(
                "Редактор закрыт; изменения оставлены в черновике",
                "warning",
            )
            self._load_selected()

    def _transcript_draft_failed(self, details: str) -> None:
        self._draft_running = False
        self._save_after_draft = False
        self._cancel_after_draft = False
        self.transcript.setEnabled(True)
        self.save_transcript_button.setEnabled(True)
        self.cancel_transcript_button.setEnabled(True)
        self.transcript_state.setStyleSheet("color: #A33636;")
        self.transcript_state.setText(self._operation_message(details, "Не удалось сохранить черновик"))

    def save_transcript_editing(self) -> None:
        if not self._transcript_editing or not self._selected_lesson_id:
            return
        self.draft_timer.stop()
        self._save_after_draft = True
        self.transcript.setEnabled(False)
        self.save_transcript_button.setEnabled(False)
        self.cancel_transcript_button.setEnabled(False)
        self.transcript_state.setText("Фиксирую последнюю версию черновика…")
        if not self._draft_running:
            self._save_transcript_draft()

    def _commit_transcript_save(self) -> None:
        lesson_id = self._selected_lesson_id
        if not lesson_id:
            return
        text = self.transcript.toPlainText()
        expected = self._transcript_base_revision
        self._save_after_draft = False
        self.transcript_state.setText("Сохраняю новую версию…")
        self.run_background(
            lambda: self.service.save_transcript(
                lesson_id,
                text,
                expected_revision_number=expected,
            ),
            self._transcript_saved,
            self._transcript_save_failed,
        )

    def _transcript_saved(self, result: object) -> None:
        if not isinstance(result, TranscriptRevision):
            self._transcript_save_failed("Некорректный результат сохранения")
            return
        self._finish_transcript_editing()
        self.status_changed.emit(f"Транскрипт сохранён · версия {result.revision_number}", "success")
        self._load_selected()

    def _transcript_save_failed(self, details: str) -> None:
        self._save_after_draft = False
        self.transcript.setEnabled(True)
        self.save_transcript_button.setEnabled(True)
        self.cancel_transcript_button.setEnabled(True)
        self.transcript_state.setStyleSheet("color: #A33636;")
        self.transcript_state.setText(
            self._operation_message(
                details,
                "Не удалось сохранить версию; черновик оставлен без изменений",
            )
        )

    def cancel_transcript_editing(self) -> None:
        if not self._transcript_editing:
            return
        self.draft_timer.stop()
        self._cancel_after_draft = True
        self.transcript.setEnabled(False)
        self.save_transcript_button.setEnabled(False)
        self.cancel_transcript_button.setEnabled(False)
        self.transcript_state.setText("Сохраняю черновик перед закрытием редактора…")
        if not self._draft_running:
            self._save_transcript_draft()

    def _finish_transcript_editing(self) -> None:
        self._transcript_editing = False
        self._save_after_draft = False
        self._cancel_after_draft = False
        self._transcript_base_revision = None
        self.table.setEnabled(True)
        self.close_details_button.setEnabled(True)
        self.details_dialog.set_close_allowed(True)
        self.delete_lesson_button.setEnabled(self._current_content is not None)
        self.transcript.setEnabled(True)
        self.transcript.setReadOnly(True)
        self.transcript_actions.setVisible(False)

    def open_revision_history(self) -> None:
        if not self._selected_lesson_id or self.history_dialog is not None:
            return
        lesson_id = self._selected_lesson_id
        self.history_button.setEnabled(False)
        self.transcript_state.setText("Загружаю историю версий…")
        self.run_background(
            lambda: self.service.list_transcript_revisions(lesson_id),
            self._revision_history_ready,
            self._revision_history_failed,
        )

    def _revision_history_ready(self, result: object) -> None:
        revisions = result
        if not isinstance(revisions, list) or not all(
            isinstance(item, TranscriptRevision) for item in revisions
        ):
            self._revision_history_failed("Некорректный результат истории")
            return
        dialog = RevisionHistoryDialog(revisions, self)
        self.history_dialog = dialog
        dialog.restore_requested.connect(
            lambda revision_id, current=dialog: self._restore_revision(current, revision_id)
        )
        dialog.finished.connect(lambda _result, current=dialog: self._history_dialog_closed(current))
        dialog.open()
        self.history_button.setEnabled(bool(revisions))
        self._restore_transcript_state()

    def _revision_history_failed(self, details: str) -> None:
        self.history_button.setEnabled(self._current_content is not None)
        self.transcript_state.setStyleSheet("color: #A33636;")
        self.transcript_state.setText(self._operation_message(details, "Не удалось загрузить историю"))

    def _restore_revision(self, dialog: RevisionHistoryDialog, revision_id: int) -> None:
        content = self._current_content
        expected = content.transcript.revision_number if content and content.transcript else None
        self.run_background(
            lambda: self.service.revert_transcript(
                revision_id,
                expected_revision_number=expected,
            ),
            lambda result, current=dialog: self._revision_restored(current, result),
            lambda details, current=dialog: current.show_error(
                self._operation_message(details, "Не удалось восстановить версию")
            ),
        )

    def _revision_restored(self, dialog: RevisionHistoryDialog, result: object) -> None:
        if not isinstance(result, TranscriptRevision):
            dialog.show_error("Некорректный результат восстановления")
            return
        dialog.accept()
        self.status_changed.emit(
            f"Версия восстановлена как новая · {result.revision_number}",
            "success",
        )
        self._load_selected()

    def _history_dialog_closed(self, dialog: RevisionHistoryDialog) -> None:
        if self.history_dialog is dialog:
            self.history_dialog = None

    def _restore_transcript_state(self) -> None:
        content = self._current_content
        if content and content.transcript:
            self.transcript_state.setStyleSheet("")
            self.transcript_state.setText(
                f"Версия {content.transcript.revision_number} · {content.transcript.created_by}"
            )

    def ensure_loaded(self) -> None:
        if self._initial_sync_started:
            return
        self._initial_sync_started = True
        self.refresh()

    def refresh_if_loaded(self) -> None:
        if self._initial_sync_started:
            self.refresh()

    def synchronize(self) -> None:
        if self._sync_running:
            return
        self._sync_running = True
        self.sync_button.setEnabled(False)
        self.loading_label.setText("Проверяю и восстанавливаю локальный архив…")
        self.status_changed.emit("Проверяю архив материалов…", "working")
        self.run_background(
            self.service.repair_archive,
            self._synchronization_ready,
            self._synchronization_failed,
        )

    def _synchronization_ready(self, result: object) -> None:
        self._sync_running = False
        self.sync_button.setEnabled(True)
        errors = getattr(result, "errors", [])
        if errors:
            self.loading_label.setText(f"Архив восстановлен с предупреждениями: {len(errors)}")
            self.status_changed.emit("Проверка архива завершена с предупреждениями", "warning")
        else:
            self.loading_label.setText("Архив проверен и восстановлен")
            self.status_changed.emit("Архив материалов проверен", "success")
        self.refresh()

    def _synchronization_failed(self, details: str) -> None:
        self._sync_running = False
        self.sync_button.setEnabled(True)
        self.loading_label.setText("Не удалось проверить локальный архив")
        self.status_changed.emit("Ошибка восстановления архива", "error")
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
        details_visible = self.details_dialog.isVisible()
        if selected_row >= 0 and details_visible:
            self.table.selectRow(selected_row)
        else:
            self.table.clearSelection()
        self.table.blockSignals(False)
        if selected_row >= 0 and details_visible:
            self._load_selected(activate=False)
        elif not self._transcript_editing:
            if details_visible:
                self.details_dialog.close()
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

    def _load_selected(self, *, activate: bool = True) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        lesson_id = str(items[0].data(Qt.UserRole))
        if not lesson_id:
            return
        if lesson_id != self._selected_lesson_id:
            self.playback_panel.stop(clear_source=True)
            self._clear_details()
        self._selected_lesson_id = lesson_id
        self.details_dialog.setWindowTitle(f"Содержимое занятия · {lesson_id}")
        if not self.details_dialog.isVisible():
            self.details_dialog.open()
        if activate:
            self.details_dialog.raise_()
            self.details_dialog.activateWindow()
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
        self._current_content = result
        lesson = result.lesson
        self.details_dialog.setWindowTitle(f"Содержимое занятия · {lesson.topic}")
        self.metadata["student"].setText(lesson.student.full_name)
        self.metadata["date"].setText(lesson.lesson_date.strftime("%d.%m.%Y"))
        self.metadata["subject"].setText(lesson.subject)
        self.metadata["topic"].setText(lesson.topic)
        self.metadata["status"].setText(status_label(lesson.status))
        self.metadata["lesson_id"].setText(lesson.lesson_id)
        self.metadata["updated"].setText(lesson.updated_at.astimezone().strftime("%d.%m.%Y %H:%M"))
        stale_labels = {"pdf": "PDF", "web": "web"}
        stale = [stale_labels.get(item.value, item.value) for item in lesson.stale_materials]
        self.metadata["materials"].setText(f"Устарели: {', '.join(stale)}" if stale else "Актуальны")
        if stale:
            self.metadata["materials"].setStyleSheet("color: #A15C00;")
        else:
            self.metadata["materials"].setStyleSheet("")
        self.edit_metadata_button.setEnabled(True)
        self.delete_lesson_button.setEnabled(True)
        self.edit_transcript_button.setEnabled(True)
        self.history_button.setEnabled(result.transcript is not None)

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
            self.transcript.blockSignals(True)
            self.transcript.setPlainText(result.transcript.content)
            self.transcript.blockSignals(False)
            transcript_path = self.workspace / result.transcript.relative_path
            state = f"Версия {result.transcript.revision_number} · {result.transcript.created_by}"
            if not transcript_path.is_file():
                state += " · исходный файл отсутствует, показана сохранённая копия SQLite"
                self.transcript_state.setStyleSheet("color: #A15C00;")
            else:
                self.transcript_state.setStyleSheet("")
            if result.draft:
                state += " · есть автосохранённый черновик"
            self.transcript_state.setText(state)
        else:
            self.transcript.blockSignals(True)
            self.transcript.clear()
            self.transcript.blockSignals(False)
            self.transcript_state.setStyleSheet("")
            self.transcript_state.setText(
                "Есть автосохранённый черновик"
                if result.draft
                else "Для занятия нет проиндексированного транскрипта"
            )

    def _detail_failed(self, request_id: int, details: str) -> None:
        if request_id != self._detail_request:
            return
        self._clear_details()
        self.transcript_state.setText("Не удалось загрузить содержимое занятия")
        self.transcript_state.setToolTip(details[-3000:])

    def _clear_details(self) -> None:
        self._current_content = None
        for label in getattr(self, "metadata", {}).values():
            label.setText("—")
        if hasattr(self, "files_table"):
            self.files_table.setRowCount(0)
        if hasattr(self, "transcript"):
            self.transcript.blockSignals(True)
            self.transcript.clear()
            self.transcript.blockSignals(False)
        if hasattr(self, "transcript_state"):
            self.transcript_state.setText("Выберите занятие")
            self.transcript_state.setStyleSheet("")
        if hasattr(self, "open_file_button"):
            self.open_file_button.setEnabled(False)
            self.open_file_button.setText("Открыть файл")
        if hasattr(self, "playback_panel"):
            self.playback_panel.reset()
        for button_name in (
            "edit_metadata_button",
            "edit_transcript_button",
            "history_button",
            "delete_lesson_button",
        ):
            button = getattr(self, button_name, None)
            if button is not None:
                button.setEnabled(False)

    def close_details(self) -> None:
        self.details_dialog.close()

    def _details_dialog_closed(self, _result: int) -> None:
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
