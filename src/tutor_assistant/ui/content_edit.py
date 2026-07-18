from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from difflib import unified_diff

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..content import TranscriptRevision
from ..domain import Lesson, Student
from .theme import set_button_kind


@dataclass(frozen=True)
class LessonMetadataEdit:
    lesson_id: str
    student: Student
    subject: str
    lesson_date: date
    topic: str
    expected_updated_at: datetime
    expected_row_version: int


class MetadataEditDialog(QDialog):
    save_requested = Signal(object)

    def __init__(
        self,
        lesson: Lesson,
        students: list[Student],
        row_version: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.lesson = lesson
        self.row_version = row_version
        self.setWindowTitle("Редактирование занятия")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        title = QLabel("Метаданные занятия")
        title.setObjectName("tileTitle")
        layout.addWidget(title)

        form = QFormLayout()
        self.student = QComboBox()
        available = {item.id: item for item in students}
        available.setdefault(lesson.student.id, lesson.student)
        for item in sorted(available.values(), key=lambda value: value.full_name.casefold()):
            self.student.addItem(item.full_name, item)
        self.student.setCurrentIndex(max(0, self.student.findData(lesson.student)))
        form.addRow("Ученик", self.student)

        self.subject = QComboBox()
        self.subject.setEditable(True)
        subjects = {lesson.subject}
        for item in available.values():
            subjects.update(item.subjects)
        self.subject.addItems(sorted(subjects, key=str.casefold))
        self.subject.setCurrentText(lesson.subject)
        form.addRow("Предмет", self.subject)

        self.topic = QLineEdit(lesson.topic)
        self.topic.setClearButtonEnabled(True)
        form.addRow("Тема", self.topic)

        self.lesson_date = QDateEdit()
        self.lesson_date.setCalendarPopup(True)
        self.lesson_date.setDisplayFormat("dd.MM.yyyy")
        self.lesson_date.setDate(
            QDate(lesson.lesson_date.year, lesson.lesson_date.month, lesson.lesson_date.day)
        )
        form.addRow("Дата", self.lesson_date)
        layout.addLayout(form)

        self.state = QLabel("")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = set_button_kind(QPushButton("Отмена"), "ghost")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.save_button = set_button_kind(QPushButton("Сохранить"), "primary")
        self.save_button.clicked.connect(self._submit)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)

    def _submit(self) -> None:
        student = self.student.currentData()
        subject = self.subject.currentText().strip()
        topic = self.topic.text().strip()
        if not isinstance(student, Student):
            self.show_error("Выберите ученика")
            return
        if not subject:
            self.show_error("Укажите предмет")
            return
        if not topic:
            self.show_error("Укажите тему занятия")
            return
        value = self.lesson_date.date()
        self.save_button.setEnabled(False)
        self.state.setStyleSheet("")
        self.state.setText("Сохраняю изменения…")
        self.save_requested.emit(
            LessonMetadataEdit(
                lesson_id=self.lesson.lesson_id,
                student=student,
                subject=subject,
                lesson_date=date(value.year(), value.month(), value.day()),
                topic=topic,
                expected_updated_at=self.lesson.updated_at,
                expected_row_version=self.row_version,
            )
        )

    def show_error(self, message: str) -> None:
        self.save_button.setEnabled(True)
        self.state.setStyleSheet("color: #A33636;")
        self.state.setText(message)


class RevisionHistoryDialog(QDialog):
    restore_requested = Signal(int)

    def __init__(self, revisions: list[TranscriptRevision], parent=None) -> None:
        super().__init__(parent)
        self.revisions = revisions
        self.by_id = {revision.id: revision for revision in revisions if revision.id is not None}
        self.setWindowTitle("История транскрипта")
        self.resize(880, 650)

        layout = QVBoxLayout(self)
        title = QLabel("История подтверждённого транскрипта")
        title.setObjectName("tileTitle")
        layout.addWidget(title)

        self.table = QTableWidget(len(revisions), 3)
        self.table.setHorizontalHeaderLabels(["Версия", "Дата", "Автор"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        for row, revision in enumerate(revisions):
            values = (
                str(revision.revision_number),
                revision.created_at.astimezone().strftime("%d.%m.%Y %H:%M"),
                revision.created_by,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, revision.id)
                self.table.setItem(row, column, item)
        self.table.horizontalHeader().setStretchLastSection(True)
        if revisions:
            self.table.selectRow(0)
        layout.addWidget(self.table, 1)

        compare_row = QHBoxLayout()
        compare_row.addWidget(QLabel("Сравнить"))
        self.first = QComboBox()
        self.second = QComboBox()
        for revision in revisions:
            label = f"Версия {revision.revision_number} · {revision.created_by}"
            self.first.addItem(label, revision.id)
            self.second.addItem(label, revision.id)
        if len(revisions) > 1:
            self.first.setCurrentIndex(1)
        compare_row.addWidget(self.first, 1)
        compare_row.addWidget(QLabel("с"))
        compare_row.addWidget(self.second, 1)
        layout.addLayout(compare_row)

        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setPlaceholderText("Выберите две версии")
        layout.addWidget(self.diff, 2)
        self.first.currentIndexChanged.connect(self._update_diff)
        self.second.currentIndexChanged.connect(self._update_diff)
        self._update_diff()

        actions = QHBoxLayout()
        self.state = QLabel("Восстановление создаёт новую версию и не удаляет историю")
        self.state.setObjectName("muted")
        actions.addWidget(self.state, 1)
        close = set_button_kind(QPushButton("Закрыть"), "ghost")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        self.restore_button = set_button_kind(QPushButton("Восстановить выбранную"), "primary")
        self.restore_button.setEnabled(bool(revisions))
        self.restore_button.clicked.connect(self._restore)
        actions.addWidget(self.restore_button)
        layout.addLayout(actions)

    def _update_diff(self) -> None:
        first = self.by_id.get(self.first.currentData())
        second = self.by_id.get(self.second.currentData())
        if first is None or second is None:
            self.diff.clear()
            return
        difference = "".join(
            unified_diff(
                first.content.splitlines(keepends=True),
                second.content.splitlines(keepends=True),
                fromfile=f"версия {first.revision_number}",
                tofile=f"версия {second.revision_number}",
            )
        )
        self.diff.setPlainText(difference or "Версии совпадают")

    def _restore(self) -> None:
        items = self.table.selectedItems()
        revision_id = items[0].data(Qt.UserRole) if items else None
        if revision_id is not None:
            self.restore_button.setEnabled(False)
            self.state.setText("Восстанавливаю как новую версию…")
            self.restore_requested.emit(int(revision_id))

    def show_error(self, message: str) -> None:
        self.restore_button.setEnabled(True)
        self.state.setStyleSheet("color: #A33636;")
        self.state.setText(message)
