from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, QSignalBlocker, Qt, QTime, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..crm import (
    CrmStore,
    Guardian,
    ScheduleConflict,
    ScheduledLesson,
    ScheduleRule,
    StudentProfile,
)
from .accessibility import sync_text_status
from .localization import (
    contact_channel_label,
    parse_subject_list,
    select_subject,
    set_subject_combo,
    subject_label,
    subject_list_text,
    subject_value,
)
from .theme import refresh_style, set_button_kind

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def _slugify(value: str) -> str:
    transliteration = str.maketrans(
        "абвгдеёзийклмнопрстуфхцчшщыэюя",
        "abvgdeezijklmnoprstufhzcssseua",
    )
    normalized = value.lower().translate(transliteration)
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")


class GuardianDialog(QDialog):
    def __init__(self, guardian: Guardian | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Контакт представителя")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.full_name = QLineEdit(guardian.full_name if guardian else "")
        self.relationship = QLineEdit(guardian.relationship if guardian else "Родитель")
        self.phone = QLineEdit(guardian.phone if guardian else "")
        self.email = QLineEdit(guardian.email if guardian else "")
        self.social = QLineEdit(guardian.social_url if guardian else "")
        self.preferred = QComboBox()
        self.preferred.addItem("Телефон", "phone")
        self.preferred.addItem("Email", "email")
        self.preferred.addItem("Социальная сеть", "social")
        if guardian:
            self.preferred.setCurrentIndex(max(0, self.preferred.findData(guardian.preferred_contact)))
        self.primary = QCheckBox("Основной контакт")
        self.primary.setChecked(bool(guardian and guardian.is_primary))
        form.addRow("ФИО", self.full_name)
        form.addRow("Кем приходится", self.relationship)
        form.addRow("Телефон", self.phone)
        form.addRow("Email", self.email)
        form.addRow("Социальная сеть", self.social)
        form.addRow("Предпочтительный канал", self.preferred)
        form.addRow("", self.primary)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.full_name.text().strip():
            QMessageBox.warning(self, "Контакт", "Укажите ФИО представителя")
            return
        self.accept()

    def value(self) -> Guardian:
        return Guardian(
            full_name=self.full_name.text().strip(),
            relationship=self.relationship.text().strip() or "Родитель",
            phone=self.phone.text().strip(),
            email=self.email.text().strip(),
            social_url=self.social.text().strip(),
            preferred_contact=str(self.preferred.currentData()),
            is_primary=self.primary.isChecked(),
        )


class StudentsPage(QWidget):
    changed = Signal()
    materials_requested = Signal(str)

    def __init__(self, store: CrmStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.guardians: list[Guardian] = []
        self.current_id: str | None = None
        self._dirty = False
        self._loading_form = False
        self._restoring_selection = False
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Ученики")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Карточки, контакты, цели и условия занятий")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по ФИО, классу или цели")
        self.search.setMaximumWidth(340)
        self.search.textChanged.connect(self.refresh)
        heading.addWidget(self.search)
        add = set_button_kind(QPushButton("Новый ученик"), "primary")
        add.clicked.connect(self.new_student)
        heading.addWidget(add)
        layout.addLayout(heading)

        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["ФИО", "Класс", "Экзамен", "Цель", "Предметы", "Ставка", "Контакт"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._load_selected)
        splitter.addWidget(self.table)

        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor = QFrame()
        editor.setObjectName("crmEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(20, 18, 20, 18)
        editor_layout.setSpacing(12)
        editor_header = QHBoxLayout()
        editor_title = QLabel("Карточка ученика")
        editor_title.setObjectName("tileTitle")
        editor_header.addWidget(editor_title, 1)
        self.dirty_label = QLabel("Все изменения сохранены")
        self.dirty_label.setObjectName("dirtyState")
        self.dirty_label.setProperty("tone", "success")
        self.dirty_label.setAccessibleName("Состояние сохранения карточки ученика")
        editor_header.addWidget(self.dirty_label)
        editor_layout.addLayout(editor_header)

        self.student_id = QLineEdit()
        self.student_id.setPlaceholderText("student_slug")
        self.full_name = QLineEdit()
        self.grade = QSpinBox()
        self.grade.setRange(0, 12)
        self.grade.setSpecialValueText("—")
        self.school = QLineEdit()
        self.exam = QComboBox()
        self.exam.setEditable(True)
        self.exam.addItems(["", "ОГЭ", "ЕГЭ", "МЦКО", "Олимпиада"])
        self.goal = QLineEdit()
        self.target_score = QSpinBox()
        self.target_score.setRange(0, 100)
        self.target_score.setSpecialValueText("—")
        self.subjects = QLineEdit()
        self.subjects.setPlaceholderText("Математика, Физика")
        self.timezone = QLineEdit("Europe/Moscow")
        self.repository_folder = QLineEdit()
        self.repository_folder.setPlaceholderText("students/student_slug")
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0, 1_000_000)
        self.rate.setDecimals(2)
        self.rate.setSuffix(" ₽")
        self.active = QCheckBox("Активный ученик")
        self.active.setChecked(True)

        regular_group = QGroupBox("Учебная карточка")
        regular_group.setObjectName("crmRegularFields")
        regular_form = QFormLayout(regular_group)
        regular_form.setVerticalSpacing(9)
        regular_form.addRow("ФИО", self.full_name)
        regular_form.addRow("Класс", self.grade)
        regular_form.addRow("Школа", self.school)
        regular_form.addRow("Экзамен", self.exam)
        regular_form.addRow("Цель", self.goal)
        regular_form.addRow("Целевой балл", self.target_score)
        regular_form.addRow("Предметы", self.subjects)
        regular_form.addRow("Ставка", self.rate)
        regular_form.addRow("", self.active)
        editor_layout.addWidget(regular_group)

        self.technical_toggle = set_button_kind(
            QPushButton("Технические параметры ▸"),
            "ghost",
        )
        self.technical_toggle.setCheckable(True)
        self.technical_toggle.setAccessibleName("Показать технические параметры ученика")
        self.technical_toggle.toggled.connect(self._toggle_technical_fields)
        editor_layout.addWidget(self.technical_toggle)

        self.technical_panel = QFrame()
        self.technical_panel.setObjectName("crmTechnicalFields")
        technical_form = QFormLayout(self.technical_panel)
        technical_form.setVerticalSpacing(9)
        technical_form.addRow("ID", self.student_id)
        technical_form.addRow("Часовой пояс", self.timezone)
        technical_form.addRow("Папка репозитория", self.repository_folder)
        self.technical_panel.setVisible(False)
        editor_layout.addWidget(self.technical_panel)

        guardian_header = QHBoxLayout()
        guardian_title = QLabel("Родители и представители")
        guardian_title.setObjectName("eyebrow")
        guardian_header.addWidget(guardian_title, 1)
        add_guardian = set_button_kind(QPushButton("Добавить"), "ghost")
        add_guardian.clicked.connect(self._add_guardian)
        guardian_header.addWidget(add_guardian)
        self.edit_guardian_button = set_button_kind(QPushButton("Изменить"), "ghost")
        self.edit_guardian_button.setEnabled(False)
        self.edit_guardian_button.clicked.connect(self._edit_guardian)
        guardian_header.addWidget(self.edit_guardian_button)
        remove_guardian = set_button_kind(QPushButton("Удалить"), "ghost")
        remove_guardian.clicked.connect(self._remove_guardian)
        guardian_header.addWidget(remove_guardian)
        editor_layout.addLayout(guardian_header)
        self.guardian_table = QTableWidget(0, 4)
        self.guardian_table.setHorizontalHeaderLabels(["ФИО", "Роль", "Телефон", "Связь"])
        self.guardian_table.horizontalHeader().setStretchLastSection(True)
        self.guardian_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.guardian_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.guardian_table.verticalHeader().setVisible(False)
        self.guardian_table.itemSelectionChanged.connect(self._sync_guardian_actions)
        self.guardian_table.doubleClicked.connect(self._edit_guardian)
        editor_layout.addWidget(self.guardian_table)

        notes_label = QLabel("Личные заметки")
        notes_label.setObjectName("eyebrow")
        editor_layout.addWidget(notes_label)
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(100)
        self.notes.setPlaceholderText("Заметки хранятся локально и защищаются Windows DPAPI")
        editor_layout.addWidget(self.notes)

        actions = QHBoxLayout()
        archive = set_button_kind(QPushButton("В архив"), "ghost")
        archive.clicked.connect(self._archive)
        actions.addWidget(archive)
        self.materials_button = set_button_kind(QPushButton("Материалы"), "ghost")
        self.materials_button.setToolTip("Открыть локальный архив занятий этого ученика")
        self.materials_button.setEnabled(False)
        self.materials_button.clicked.connect(self._open_materials)
        actions.addWidget(self.materials_button)
        actions.addStretch(1)
        self.save_button = set_button_kind(QPushButton("Сохранить карточку"), "primary")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        editor_layout.addLayout(actions)
        editor_layout.addStretch(1)
        editor_scroll.setWidget(editor)
        splitter.addWidget(editor_scroll)
        splitter.setSizes([650, 500])
        layout.addWidget(splitter, 1)
        self._connect_dirty_tracking()

    def _connect_dirty_tracking(self) -> None:
        for widget in (
            self.student_id,
            self.full_name,
            self.school,
            self.goal,
            self.subjects,
            self.timezone,
            self.repository_folder,
        ):
            widget.textEdited.connect(self._mark_dirty)
        self.grade.valueChanged.connect(self._mark_dirty)
        self.exam.currentTextChanged.connect(self._mark_dirty)
        self.target_score.valueChanged.connect(self._mark_dirty)
        self.rate.valueChanged.connect(self._mark_dirty)
        self.active.toggled.connect(self._mark_dirty)
        self.notes.textChanged.connect(self._mark_dirty)

    def _mark_dirty(self, *_args) -> None:
        if not self._loading_form:
            self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.dirty_label.setText(
            "Есть несохранённые изменения" if dirty else "Все изменения сохранены"
        )
        self.dirty_label.setProperty("tone", "warning" if dirty else "success")
        self.save_button.setEnabled(dirty)
        sync_text_status(self.dirty_label, "Состояние сохранения карточки ученика")
        refresh_style(self.dirty_label)

    def _toggle_technical_fields(self, expanded: bool) -> None:
        self.technical_panel.setVisible(expanded)
        self.technical_toggle.setText(
            "Технические параметры ▾" if expanded else "Технические параметры ▸"
        )
        self.technical_toggle.setAccessibleName(
            "Скрыть технические параметры ученика"
            if expanded
            else "Показать технические параметры ученика"
        )

    def _confirm_card_transition(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Несохранённые изменения",
            "Карточка ученика изменена. Сохранить изменения перед переходом?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Save:
            return self._save()
        if answer == QMessageBox.Discard:
            self._set_dirty(False)
            return True
        return False

    def _restore_current_selection(self) -> None:
        self._restoring_selection = True
        self.table.blockSignals(True)
        try:
            if self.current_id is None:
                self.table.clearSelection()
                return
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item and item.data(Qt.UserRole) == self.current_id:
                    self.table.selectRow(row)
                    return
        finally:
            self.table.blockSignals(False)
            self._restoring_selection = False

    def refresh(self) -> None:
        query = self.search.text().strip().casefold() if hasattr(self, "search") else ""
        profiles = self.store.list_students()
        if query:
            profiles = [
                item
                for item in profiles
                if query
                in " ".join(
                    [item.full_name, str(item.grade or ""), item.exam, item.goal, *item.subjects]
                ).casefold()
            ]
        self.table.setRowCount(len(profiles))
        for row, profile in enumerate(profiles):
            guardians = self.store.list_guardians(profile.id)
            primary = next(
                (item for item in guardians if item.is_primary),
                guardians[0] if guardians else None,
            )
            values = [
                profile.full_name,
                str(profile.grade or "—"),
                profile.exam or "—",
                profile.goal or "—",
                subject_list_text(profile.subjects) or "—",
                f"{profile.default_rate_cents / 100:,.0f} ₽" if profile.default_rate_cents else "—",
                primary.phone if primary else "—",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, profile.id)
                self.table.setItem(row, column, item)
        if self.current_id:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).data(Qt.UserRole) == self.current_id:
                    self.table.selectRow(row)
                    break

    def new_student(self, _checked: bool = False, *, force: bool = False) -> None:
        if not force and not self._confirm_card_transition():
            return
        self._loading_form = True
        self.current_id = None
        self.materials_button.setEnabled(False)
        self.student_id.setEnabled(True)
        text_fields = (
            self.student_id,
            self.full_name,
            self.school,
            self.goal,
            self.subjects,
            self.repository_folder,
        )
        for widget in text_fields:
            widget.clear()
        self.grade.setValue(0)
        self.exam.setCurrentText("")
        self.target_score.setValue(0)
        self.timezone.setText("Europe/Moscow")
        self.rate.setValue(0)
        self.active.setChecked(True)
        self.notes.clear()
        self.guardians = []
        self._render_guardians()
        self._loading_form = False
        self._set_dirty(False)
        self.full_name.setFocus()

    def _load_selected(self) -> None:
        if self._restoring_selection:
            return
        items = self.table.selectedItems()
        if not items:
            return
        student_id = str(items[0].data(Qt.UserRole))
        if student_id == self.current_id:
            return
        if not self._confirm_card_transition():
            self._restore_current_selection()
            return
        profile = self.store.get_student(student_id)
        if profile is None:
            return
        self._loading_form = True
        self.current_id = profile.id
        self.materials_button.setEnabled(True)
        self.student_id.setText(profile.id)
        self.student_id.setEnabled(False)
        self.full_name.setText(profile.full_name)
        self.grade.setValue(profile.grade or 0)
        self.school.setText(profile.school)
        self.exam.setCurrentText(profile.exam)
        self.goal.setText(profile.goal)
        self.target_score.setValue(profile.target_score or 0)
        self.subjects.setText(subject_list_text(profile.subjects))
        self.timezone.setText(profile.timezone)
        self.repository_folder.setText(profile.repository_folder or "")
        self.rate.setValue(profile.default_rate_cents / 100)
        self.active.setChecked(profile.active)
        self.notes.setPlainText(profile.notes)
        self.guardians = self.store.list_guardians(profile.id)
        self._render_guardians()
        self._loading_form = False
        self._set_dirty(False)

    def _render_guardians(self) -> None:
        self.guardian_table.setRowCount(len(self.guardians))
        for row, guardian in enumerate(self.guardians):
            values = [
                ("★ " if guardian.is_primary else "") + guardian.full_name,
                guardian.relationship,
                guardian.phone,
                contact_channel_label(guardian.preferred_contact),
            ]
            for column, value in enumerate(values):
                self.guardian_table.setItem(row, column, QTableWidgetItem(value))
        self._sync_guardian_actions()

    def _sync_guardian_actions(self) -> None:
        self.edit_guardian_button.setEnabled(self.guardian_table.currentRow() >= 0)

    def _add_guardian(self) -> None:
        dialog = GuardianDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            if dialog.value().is_primary:
                self.guardians = [item.model_copy(update={"is_primary": False}) for item in self.guardians]
            self.guardians.append(dialog.value())
            self._render_guardians()
            self._set_dirty(True)

    def _edit_guardian(self) -> None:
        row = self.guardian_table.currentRow()
        if row < 0:
            return
        dialog = GuardianDialog(self.guardians[row], self)
        if dialog.exec() == QDialog.Accepted:
            updated = dialog.value()
            if updated.is_primary:
                self.guardians = [item.model_copy(update={"is_primary": False}) for item in self.guardians]
            self.guardians[row] = updated
            self._render_guardians()
            self._set_dirty(True)

    def _remove_guardian(self) -> None:
        row = self.guardian_table.currentRow()
        if row >= 0:
            self.guardians.pop(row)
            self._render_guardians()
            self._set_dirty(True)

    def _save(self) -> bool:
        name = self.full_name.text().strip()
        student_id = self.student_id.text().strip() or _slugify(name)
        if not name or not student_id:
            QMessageBox.warning(self, "Карточка", "Укажите ФИО и ID ученика")
            return False
        try:
            profile = StudentProfile(
                id=student_id,
                full_name=name,
                grade=self.grade.value() or None,
                school=self.school.text().strip(),
                goal=self.goal.text().strip(),
                exam=self.exam.currentText().strip(),
                target_score=self.target_score.value() or None,
                subjects=parse_subject_list(self.subjects.text()),
                timezone=self.timezone.text().strip() or "Europe/Moscow",
                repository_folder=self.repository_folder.text().strip() or None,
                default_rate_cents=round(self.rate.value() * 100),
                notes=self.notes.toPlainText().strip(),
                active=self.active.isChecked(),
            )
            self.store.save_student(profile, self.guardians)
        except Exception as exc:
            QMessageBox.critical(self, "Карточка", str(exc))
            return False
        self.current_id = student_id
        self.materials_button.setEnabled(True)
        self.student_id.setEnabled(False)
        self._set_dirty(False)
        self.refresh()
        self.changed.emit()
        return True

    def _archive(self) -> None:
        if not self.current_id:
            return
        if not self._confirm_card_transition():
            return
        if QMessageBox.question(self, "Архив", "Переместить ученика в архив?") == QMessageBox.Yes:
            self.store.archive_student(self.current_id)
            self.current_id = None
            self.new_student(force=True)
            self.refresh()
            self.changed.emit()

    def _open_materials(self) -> None:
        if self.current_id:
            self.materials_requested.emit(self.current_id)


class ScheduleDialog(QDialog):
    def __init__(
        self,
        store: CrmStore,
        selected_date: date,
        selected_hour: int = 16,
        selected_minute: int = 0,
        lesson: ScheduledLesson | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.lesson = lesson
        self.action = "cancel"
        self.setWindowTitle("Занятие в расписании")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        title = QLabel(lesson.student_name if lesson else "Новое занятие")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        form = QFormLayout()
        self.student = QComboBox()
        for profile in store.list_students():
            self.student.addItem(profile.full_name, profile.id)
        self.lesson_date = QDateEdit()
        self.lesson_date.setCalendarPopup(True)
        self.lesson_date.setDate(QDate(selected_date.year, selected_date.month, selected_date.day))
        self.start_time = QTimeEdit(QTime(selected_hour, selected_minute))
        self.start_time.setDisplayFormat("HH:mm")
        self.start_time.setTimeRange(QTime(0, 0), QTime(23, 30))
        self.start_time.editingFinished.connect(self._snap_start_time)
        self.duration = QComboBox()
        for minutes in (60, 90, 120):
            self.duration.addItem(f"{minutes} минут", minutes)
        self.subject = QComboBox()
        self.subject.setEditable(True)
        set_subject_combo(self.subject)
        self.topic = QLineEdit()
        self.meeting = QLineEdit()
        self.meeting.setPlaceholderText("Ссылка на видеозвонок")
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0, 1_000_000)
        self.rate.setSuffix(" ₽")
        self.recurring = QCheckBox("Повторять каждую неделю")
        self.recurring.setChecked(True)
        form.addRow("Ученик", self.student)
        form.addRow("Дата", self.lesson_date)
        form.addRow("Время", self.start_time)
        form.addRow("Длительность", self.duration)
        form.addRow("Предмет", self.subject)
        form.addRow("Тема", self.topic)
        form.addRow("Ссылка", self.meeting)
        form.addRow("Ставка", self.rate)
        form.addRow("", self.recurring)
        layout.addLayout(form)

        if lesson:
            self.student.setCurrentIndex(max(0, self.student.findData(lesson.student_id)))
            self.lesson_date.setDate(
                QDate(lesson.starts_at.year, lesson.starts_at.month, lesson.starts_at.day)
            )
            self.start_time.setTime(QTime(lesson.starts_at.hour, lesson.starts_at.minute))
            self.duration.setCurrentIndex(max(0, self.duration.findData(lesson.duration_minutes)))
            select_subject(self.subject, lesson.subject)
            self.topic.setText(lesson.topic)
            self.meeting.setText(lesson.meeting_url)
            self.rate.setValue(lesson.rate_cents / 100)
            self.recurring.setChecked(lesson.rule_id is not None)

        actions = QHBoxLayout()
        if lesson:
            start = set_button_kind(QPushButton("Начать запись"), "primary")
            start.clicked.connect(lambda: self._finish("start"))
            actions.addWidget(start)
            delete = set_button_kind(QPushButton("Удалить"), "danger")
            delete.clicked.connect(lambda: self._finish("delete"))
            actions.addWidget(delete)
        actions.addStretch(1)
        cancel = set_button_kind(QPushButton("Отмена"), "ghost")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        save = set_button_kind(QPushButton("Сохранить"), "primary")
        save.clicked.connect(lambda: self._finish("save"))
        actions.addWidget(save)
        layout.addLayout(actions)
        self.student.currentIndexChanged.connect(self._student_changed)
        if not lesson:
            self._student_changed()

    def _snap_start_time(self) -> None:
        clock = self.start_time.time()
        total = clock.hour() * 60 + clock.minute()
        rounded = min(23 * 60 + 30, ((total + 15) // 30) * 30)
        self.start_time.setTime(QTime(rounded // 60, rounded % 60))

    def _student_changed(self) -> None:
        profile = self.store.get_student(str(self.student.currentData()))
        if profile:
            self.rate.setValue(profile.default_rate_cents / 100)
            if profile.subjects:
                select_subject(self.subject, profile.subjects[0])
            if not self.topic.text() and profile.goal:
                self.topic.setPlaceholderText(profile.goal)

    def _finish(self, action: str) -> None:
        if self.student.currentData() is None:
            QMessageBox.warning(self, "Расписание", "Сначала создайте карточку ученика")
            return
        self.action = action
        self.accept()

    def value(self) -> ScheduledLesson:
        value = self.lesson_date.date()
        clock = self.start_time.time()
        starts_at = datetime(value.year(), value.month(), value.day(), clock.hour(), clock.minute())
        profile = self.store.get_student(str(self.student.currentData()))
        return ScheduledLesson(
            occurrence_id=self.lesson.occurrence_id if self.lesson else None,
            rule_id=self.lesson.rule_id if self.lesson else None,
            original_date=self.lesson.original_date if self.lesson else None,
            student_id=str(self.student.currentData()),
            student_name=profile.full_name if profile else self.student.currentText(),
            starts_at=starts_at,
            duration_minutes=int(self.duration.currentData()),
            subject=subject_value(self.subject.currentText()),
            topic=self.topic.text().strip(),
            meeting_url=self.meeting.text().strip(),
            status=self.lesson.status if self.lesson else "planned",
            rate_cents=round(self.rate.value() * 100),
            paid=self.lesson.paid if self.lesson else False,
            lesson_id=self.lesson.lesson_id if self.lesson else None,
        )


class SchedulePage(QWidget):
    start_requested = Signal(int, str, str, str)

    first_hour = 8
    last_hour = 23
    slot_minutes = 30

    def __init__(self, store: CrmStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        today = date.today()
        self.week_start = today - timedelta(days=today.weekday())
        self.cell_lessons: dict[tuple[int, int], ScheduledLesson] = {}
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Расписание")
        title.setObjectName("pageTitle")
        self.week_label = QLabel()
        self.week_label.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(self.week_label)
        header.addLayout(title_box, 1)
        previous = set_button_kind(QPushButton("←"), "ghost")
        previous.setToolTip("Предыдущая неделя")
        previous.clicked.connect(lambda: self._shift_week(-7))
        header.addWidget(previous)
        today_button = set_button_kind(QPushButton("Сегодня"), "ghost")
        today_button.clicked.connect(self._today)
        header.addWidget(today_button)
        next_button = set_button_kind(QPushButton("→"), "ghost")
        next_button.setToolTip("Следующая неделя")
        next_button.clicked.connect(lambda: self._shift_week(7))
        header.addWidget(next_button)
        add = set_button_kind(QPushButton("Добавить занятие"), "primary")
        add.clicked.connect(lambda: self._open_dialog(self.week_start, 16, 0))
        header.addWidget(add)
        self.open_selected_button = set_button_kind(
            QPushButton("Открыть выбранное"),
            "ghost",
        )
        self.open_selected_button.setEnabled(False)
        self.open_selected_button.clicked.connect(self._open_selected_cell)
        header.addWidget(self.open_selected_button)
        layout.addLayout(header)

        stats = QHBoxLayout()
        self.students_stat = QLabel()
        self.lessons_stat = QLabel()
        self.revenue_stat = QLabel()
        for widget in (self.students_stat, self.lessons_stat, self.revenue_stat):
            widget.setObjectName("statusPill")
            stats.addWidget(widget)
        stats.addStretch(1)
        layout.addLayout(stats)

        self.grid = QTableWidget(self._row_count(), 7)
        self.grid.setObjectName("scheduleGrid")
        self.grid.verticalHeader().setVisible(True)
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.grid.setSelectionMode(QTableWidget.SingleSelection)
        self.grid.setEditTriggers(QTableWidget.NoEditTriggers)
        self.grid.setShowGrid(False)
        self.grid.currentCellChanged.connect(self._sync_schedule_action)
        self.grid.cellDoubleClicked.connect(self._cell_opened)
        self.grid.itemChanged.connect(self._payment_state_changed)
        for row in range(self._row_count()):
            hour, minute = self._time_for_row(row)
            self.grid.setVerticalHeaderItem(row, QTableWidgetItem(f"{hour:02d}:{minute:02d}"))
            self.grid.setRowHeight(row, 34)
        layout.addWidget(self.grid, 1)

    @classmethod
    def _row_count(cls) -> int:
        return ((cls.last_hour - cls.first_hour + 1) * 60) // cls.slot_minutes

    @classmethod
    def _row_for_time(cls, hour: int, minute: int) -> int:
        offset = hour * 60 + minute - cls.first_hour * 60
        return max(0, int(round(offset / cls.slot_minutes)))

    @classmethod
    def _time_for_row(cls, row: int) -> tuple[int, int]:
        total = cls.first_hour * 60 + row * cls.slot_minutes
        return divmod(total, 60)

    def refresh(self) -> None:
        end = self.week_start + timedelta(days=6)
        self.week_label.setText(
            f"{self.week_start:%d.%m.%Y} — {end:%d.%m.%Y} · выберите ячейку для действия"
        )
        signal_blocker = QSignalBlocker(self.grid)
        self.grid.clearSpans()
        self.grid.clearContents()
        self.cell_lessons.clear()
        today = date.today()
        for column, day_name in enumerate(WEEKDAYS):
            value = self.week_start + timedelta(days=column)
            self.grid.setHorizontalHeaderItem(
                column,
                QTableWidgetItem(f"{day_name}\n{value:%d.%m}"),
            )
            if value == today:
                self.grid.horizontalHeaderItem(column).setForeground(QColor("#356BC4"))
        colors = {
            "planned": QColor("#EAF2FF"),
            "in_progress": QColor("#FFF1CC"),
            "completed": QColor("#E8F7F0"),
            "cancelled": QColor("#F2F4F7"),
        }
        status_names = {
            "planned": "Запланировано",
            "in_progress": "Идёт занятие",
            "completed": "Завершено",
            "cancelled": "Отменено",
        }
        for lesson in self.store.lessons_for_week(self.week_start):
            row = self._row_for_time(lesson.starts_at.hour, lesson.starts_at.minute)
            column = lesson.starts_at.weekday()
            if not (0 <= row < self.grid.rowCount()):
                continue
            row_span = max(1, (lesson.duration_minutes + self.slot_minutes - 1) // self.slot_minutes)
            row_span = min(row_span, self.grid.rowCount() - row)
            payment_label = "Оплачено" if lesson.paid else "Не оплачено"
            item = QTableWidgetItem(
                f"{lesson.starts_at:%H:%M}–{lesson.ends_at:%H:%M}  {lesson.student_name}\n"
                f"{subject_label(lesson.subject)}"
                + (f" · {lesson.topic}" if lesson.topic else "")
                + f"\n{status_names.get(lesson.status, lesson.status)} · {payment_label}"
            )
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
            item.setToolTip(
                f"{lesson.student_name}\n{lesson.starts_at:%d.%m %H:%M}"
                f"–{lesson.ends_at:%H:%M}\n{lesson.topic or lesson.subject}\n"
                f"Оплата: {payment_label}\n"
                "Галочка в слоте отмечает оплату занятия. "
                "Выберите занятие и нажмите «Открыть занятие»"
            )
            item.setCheckState(
                Qt.CheckState.Checked if lesson.paid else Qt.CheckState.Unchecked
            )
            if lesson.status == "cancelled":
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                item.setBackground(colors["cancelled"])
            elif not lesson.paid:
                item.setBackground(QColor("#FFF0F0"))
                item.setForeground(QColor("#9B2C2C"))
            else:
                item.setBackground(colors.get(lesson.status, QColor("#FFFFFF")))
            self.grid.setItem(row, column, item)
            if row_span > 1:
                self.grid.setSpan(row, column, row_span, 1)
            for occupied_row in range(row, row + row_span):
                self.cell_lessons[(occupied_row, column)] = lesson
        stats = self.store.stats(self.week_start)
        self.students_stat.setText(f"Ученики · {stats.active_students}")
        self.lessons_stat.setText(f"Занятия · {stats.lessons_this_week}")
        self.revenue_stat.setText(f"План · {stats.planned_revenue_cents / 100:,.0f} ₽")
        del signal_blocker
        self._sync_schedule_action()

    def _payment_state_changed(self, item: QTableWidgetItem) -> None:
        lesson = self.cell_lessons.get((item.row(), item.column()))
        if lesson is None or lesson.status == "cancelled":
            return
        paid = item.checkState() == Qt.CheckState.Checked
        if paid == lesson.paid:
            return
        previous = lesson.paid
        try:
            occurrence_id = self.store.set_lesson_paid(lesson, paid)
        except Exception as exc:
            signal_blocker = QSignalBlocker(self.grid)
            item.setCheckState(
                Qt.CheckState.Checked if previous else Qt.CheckState.Unchecked
            )
            del signal_blocker
            QMessageBox.critical(
                self,
                "Оплата занятия",
                f"Не удалось сохранить состояние оплаты: {exc}",
            )
            return
        lesson.occurrence_id = occurrence_id
        lesson.paid = paid
        self.refresh()

    def _sync_schedule_action(self, *_args) -> None:
        row = self.grid.currentRow()
        column = self.grid.currentColumn()
        selected = row >= 0 and column >= 0
        self.open_selected_button.setEnabled(selected)
        self.open_selected_button.setText(
            "Открыть занятие"
            if selected and (row, column) in self.cell_lessons
            else "Создать в выбранное время"
        )

    def _open_selected_cell(self) -> None:
        row = self.grid.currentRow()
        column = self.grid.currentColumn()
        if row >= 0 and column >= 0:
            self._cell_opened(row, column)

    def _shift_week(self, days: int) -> None:
        self.week_start += timedelta(days=days)
        self.refresh()

    def _today(self) -> None:
        today = date.today()
        self.week_start = today - timedelta(days=today.weekday())
        self.refresh()

    def _cell_opened(self, row: int, column: int) -> None:
        selected_date = self.week_start + timedelta(days=column)
        selected_hour, selected_minute = self._time_for_row(row)
        self._open_dialog(
            selected_date,
            selected_hour,
            selected_minute,
            self.cell_lessons.get((row, column)),
        )

    def _open_dialog(
        self,
        selected_date: date,
        selected_hour: int,
        selected_minute: int = 0,
        lesson: ScheduledLesson | None = None,
    ) -> None:
        dialog = ScheduleDialog(
            self.store,
            selected_date,
            selected_hour,
            selected_minute,
            lesson,
            self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        value = dialog.value()
        try:
            if dialog.action == "start":
                occurrence_id = self.store.ensure_occurrence(value)
                self.start_requested.emit(
                    occurrence_id,
                    value.student_id,
                    value.subject,
                    value.topic or value.subject,
                )
                return
            if dialog.action == "delete":
                if value.rule_id is not None and dialog.recurring.isChecked():
                    self.store.delete_schedule_rule(value.rule_id)
                else:
                    occurrence_id = self.store.ensure_occurrence(value)
                    self.store.update_occurrence(occurrence_id, status="cancelled")
            elif dialog.recurring.isChecked():
                existing_rule = next(
                    (item for item in self.store.list_schedule_rules() if item.id == value.rule_id),
                    None,
                )
                self.store.save_schedule_rule(
                    ScheduleRule(
                        id=value.rule_id,
                        student_id=value.student_id,
                        weekday=value.starts_at.weekday(),
                        start_minute=value.starts_at.hour * 60 + value.starts_at.minute,
                        duration_minutes=value.duration_minutes,
                        subject=value.subject,
                        topic=value.topic,
                        meeting_url=value.meeting_url,
                        valid_from=existing_rule.valid_from
                        if existing_rule
                        else value.starts_at.date(),
                        valid_until=existing_rule.valid_until if existing_rule else None,
                        rate_cents=value.rate_cents,
                    )
                )
            elif lesson:
                occurrence_id = self.store.ensure_occurrence(value)
                self.store.update_occurrence_details(occurrence_id, value)
            else:
                self.store.save_one_off(value)
        except ScheduleConflict as exc:
            QMessageBox.warning(self, "Конфликт расписания", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Расписание", str(exc))
            return
        self.refresh()
