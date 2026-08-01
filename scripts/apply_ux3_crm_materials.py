from __future__ import annotations

import re
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one marker, found {count}: {old[:100]!r}")
    write(path, content.replace(old, new, 1))


def replace_re(path: str, pattern: str, replacement: str) -> None:
    content = read(path)
    updated, count = re.subn(pattern, replacement, content, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{path}: regex marker count {count}: {pattern[:100]!r}")
    write(path, updated)


# ---------------------------------------------------------------------------
# CRM: dirty state, field hierarchy and 30-minute schedule.
# ---------------------------------------------------------------------------
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "    QFormLayout,\n    QFrame,\n",
    "    QFormLayout,\n    QFrame,\n    QGroupBox,\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "from .theme import set_button_kind\n",
    "from .theme import refresh_style, set_button_kind\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        self.guardians: list[Guardian] = []
        self.current_id: str | None = None
        self._build()
        self.refresh()
''',
    '''        self.guardians: list[Guardian] = []
        self.current_id: str | None = None
        self._dirty = False
        self._loading_form = False
        self._restoring_selection = False
        self._build()
        self.refresh()
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        editor_title = QLabel("Карточка ученика")
        editor_title.setObjectName("tileTitle")
        editor_layout.addWidget(editor_title)

        form = QFormLayout()
        form.setVerticalSpacing(9)
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
        form.addRow("ID", self.student_id)
        form.addRow("ФИО", self.full_name)
        form.addRow("Класс", self.grade)
        form.addRow("Школа", self.school)
        form.addRow("Экзамен", self.exam)
        form.addRow("Цель", self.goal)
        form.addRow("Целевой балл", self.target_score)
        form.addRow("Предметы", self.subjects)
        form.addRow("Часовой пояс", self.timezone)
        form.addRow("Папка репозитория", self.repository_folder)
        form.addRow("Ставка", self.rate)
        form.addRow("", self.active)
        editor_layout.addLayout(form)
''',
    '''        editor_header = QHBoxLayout()
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
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        save = set_button_kind(QPushButton("Сохранить карточку"), "primary")
        save.clicked.connect(self._save)
        actions.addWidget(save)
        editor_layout.addLayout(actions)
        editor_layout.addStretch(1)
        editor_scroll.setWidget(editor)
        splitter.addWidget(editor_scroll)
        splitter.setSizes([650, 500])
        layout.addWidget(splitter, 1)
''',
    '''        self.save_button = set_button_kind(QPushButton("Сохранить карточку"), "primary")
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
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''    def refresh(self) -> None:
''',
    '''    def _connect_dirty_tracking(self) -> None:
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
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''    def new_student(self) -> None:
        self.current_id = None
        self.materials_button.setEnabled(False)
        self.student_id.setEnabled(True)
''',
    '''    def new_student(self, _checked: bool = False, *, force: bool = False) -> None:
        if not force and not self._confirm_card_transition():
            return
        self._loading_form = True
        self.current_id = None
        self.materials_button.setEnabled(False)
        self.student_id.setEnabled(True)
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        self.guardians = []
        self._render_guardians()
        self.full_name.setFocus()

    def _load_selected(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        student_id = str(items[0].data(Qt.UserRole))
        profile = self.store.get_student(student_id)
        if profile is None:
            return
        self.current_id = profile.id
''',
    '''        self.guardians = []
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
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        self.guardians = self.store.list_guardians(profile.id)
        self._render_guardians()

    def _render_guardians(self) -> None:
''',
    '''        self.guardians = self.store.list_guardians(profile.id)
        self._render_guardians()
        self._loading_form = False
        self._set_dirty(False)

    def _render_guardians(self) -> None:
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''            self.guardians.append(dialog.value())
            self._render_guardians()
''',
    '''            self.guardians.append(dialog.value())
            self._render_guardians()
            self._set_dirty(True)
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''            self.guardians[row] = updated
            self._render_guardians()
''',
    '''            self.guardians[row] = updated
            self._render_guardians()
            self._set_dirty(True)
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        if row >= 0:
            self.guardians.pop(row)
            self._render_guardians()

    def _save(self) -> None:
''',
    '''        if row >= 0:
            self.guardians.pop(row)
            self._render_guardians()
            self._set_dirty(True)

    def _save(self) -> bool:
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''            QMessageBox.warning(self, "Карточка", "Укажите ФИО и ID ученика")
            return
''',
    '''            QMessageBox.warning(self, "Карточка", "Укажите ФИО и ID ученика")
            return False
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        except Exception as exc:
            QMessageBox.critical(self, "Карточка", str(exc))
            return
        self.current_id = student_id
''',
    '''        except Exception as exc:
            QMessageBox.critical(self, "Карточка", str(exc))
            return False
        self.current_id = student_id
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        self.student_id.setEnabled(False)
        self.refresh()
        self.changed.emit()

    def _archive(self) -> None:
''',
    '''        self.student_id.setEnabled(False)
        self._set_dirty(False)
        self.refresh()
        self.changed.emit()
        return True

    def _archive(self) -> None:
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''    def _archive(self) -> None:
        if not self.current_id:
            return
        if QMessageBox.question(self, "Архив", "Переместить ученика в архив?") == QMessageBox.Yes:
            self.store.archive_student(self.current_id)
            self.current_id = None
            self.new_student()
''',
    '''    def _archive(self) -> None:
        if not self.current_id:
            return
        if not self._confirm_card_transition():
            return
        if QMessageBox.question(self, "Архив", "Переместить ученика в архив?") == QMessageBox.Yes:
            self.store.archive_student(self.current_id)
            self.current_id = None
            self.new_student(force=True)
''',
)

replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        selected_hour: int = 16,
        lesson: ScheduledLesson | None = None,
''',
    '''        selected_hour: int = 16,
        selected_minute: int = 0,
        lesson: ScheduledLesson | None = None,
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        self.start_time = QTimeEdit(QTime(selected_hour, 0))
''',
    '''        self.start_time = QTimeEdit(QTime(selected_hour, selected_minute))
        self.start_time.setDisplayFormat("HH:mm")
        self.start_time.setTimeRange(QTime(0, 0), QTime(23, 30))
        self.start_time.editingFinished.connect(self._snap_start_time)
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''    def _student_changed(self) -> None:
''',
    '''    def _snap_start_time(self) -> None:
        clock = self.start_time.time()
        total = clock.hour() * 60 + clock.minute()
        rounded = min(23 * 60 + 30, ((total + 15) // 30) * 30)
        self.start_time.setTime(QTime(rounded // 60, rounded % 60))

    def _student_changed(self) -> None:
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''    first_hour = 8
    last_hour = 23
''',
    '''    first_hour = 8
    last_hour = 23
    slot_minutes = 30
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        add = set_button_kind(QPushButton("Добавить занятие"), "primary")
        add.clicked.connect(lambda: self._open_dialog(self.week_start, 16))
''',
    '''        add = set_button_kind(QPushButton("Добавить занятие"), "primary")
        add.clicked.connect(lambda: self._open_dialog(self.week_start, 16, 0))
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        self.grid = QTableWidget(self.last_hour - self.first_hour + 1, 7)
''',
    '''        self.grid = QTableWidget(self._row_count(), 7)
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        for row, hour in enumerate(range(self.first_hour, self.last_hour + 1)):
            self.grid.setVerticalHeaderItem(row, QTableWidgetItem(f"{hour:02d}:00"))
            self.grid.setRowHeight(row, 58)
        layout.addWidget(self.grid, 1)

    def refresh(self) -> None:
''',
    '''        for row in range(self._row_count()):
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
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        self.grid.clearContents()
        self.cell_lessons.clear()
''',
    '''        self.grid.clearSpans()
        self.grid.clearContents()
        self.cell_lessons.clear()
''',
)
replace_re(
    "src/tutor_assistant/ui/crm.py",
    r'''        colors = \{
            "planned": QColor\("#EAF2FF"\),
            "in_progress": QColor\("#FFF1CC"\),
            "completed": QColor\("#E8F7F0"\),
            "cancelled": QColor\("#F2F4F7"\),
        \}
        for lesson in self\.store\.lessons_for_week\(self\.week_start\):
            row = lesson\.starts_at\.hour - self\.first_hour
            column = lesson\.starts_at\.weekday\(\)
            if not \(0 <= row < self\.grid\.rowCount\(\)\):
                continue
            item = QTableWidgetItem\(
                f"\{lesson\.starts_at:%H:%M\}  \{lesson\.student_name\}\\n\{subject_label\(lesson\.subject\)\}"
                \+ \(f" · \{lesson\.topic\}" if lesson\.topic else ""\)
            \)
            item\.setToolTip\(
                f"\{lesson\.student_name\}\\n\{lesson\.starts_at:%d.%m %H:%M\}"
                f"–\{lesson\.ends_at:%H:%M\}\\n\{lesson\.topic or lesson\.subject\}\\n"
                "Выберите ячейку и нажмите «Открыть выбранное»"
            \)
            item\.setBackground\(colors\.get\(lesson\.status, QColor\("#FFFFFF"\)\)\)
            self\.grid\.setItem\(row, column, item\)
            self\.cell_lessons\[\(row, column\)\] = lesson
''',
    '''        colors = {
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
            item = QTableWidgetItem(
                f"{lesson.starts_at:%H:%M}–{lesson.ends_at:%H:%M}  {lesson.student_name}\\n"
                f"{subject_label(lesson.subject)}"
                + (f" · {lesson.topic}" if lesson.topic else "")
                + f"\\n{status_names.get(lesson.status, lesson.status)}"
            )
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
            item.setToolTip(
                f"{lesson.student_name}\\n{lesson.starts_at:%d.%m %H:%M}"
                f"–{lesson.ends_at:%H:%M}\\n{lesson.topic or lesson.subject}\\n"
                "Выберите занятие и нажмите «Открыть занятие»"
            )
            item.setBackground(colors.get(lesson.status, QColor("#FFFFFF")))
            self.grid.setItem(row, column, item)
            if row_span > 1:
                self.grid.setSpan(row, column, row_span, 1)
            for occupied_row in range(row, row + row_span):
                self.cell_lessons[(occupied_row, column)] = lesson
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''    def _cell_opened(self, row: int, column: int) -> None:
        selected_date = self.week_start + timedelta(days=column)
        self._open_dialog(selected_date, self.first_hour + row, self.cell_lessons.get((row, column)))

    def _open_dialog(
        self, selected_date: date, selected_hour: int, lesson: ScheduledLesson | None = None
    ) -> None:
        dialog = ScheduleDialog(self.store, selected_date, selected_hour, lesson, self)
''',
    '''    def _cell_opened(self, row: int, column: int) -> None:
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
''',
)

# ---------------------------------------------------------------------------
# Materials: embedded split view and maintenance menu.
# ---------------------------------------------------------------------------
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    "    QMessageBox,\n    QPlainTextEdit,\n",
    "    QMessageBox,\n    QMenu,\n    QPlainTextEdit,\n",
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    "    QPushButton,\n    QTableWidget,\n",
    "    QPushButton,\n    QScrollArea,\n    QSplitter,\n    QTableWidget,\n",
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''    trash_retention_changed = Signal(int)
''',
    '''    trash_retention_changed = Signal(int)
    content_changed = Signal()
''',
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''        self.trash_button = set_button_kind(QPushButton("Корзина"), "ghost")
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
''',
    '''        self.maintenance_button = set_button_kind(QPushButton("Обслуживание"), "ghost")
        self.maintenance_button.setAccessibleName("Меню обслуживания архива материалов")
        self.maintenance_menu = QMenu(self.maintenance_button)
        self.trash_action = self.maintenance_menu.addAction("Корзина")
        self.trash_action.setShortcut(QKeySequence("Ctrl+Shift+Delete"))
        self.trash_action.triggered.connect(self.open_trash)
        self.health_action = self.maintenance_menu.addAction("Диагностика архива")
        self.health_action.setShortcut(QKeySequence("Ctrl+Shift+D"))
        self.health_action.triggered.connect(self.open_content_health)
        self.maintenance_menu.addSeparator()
        self.sync_action = self.maintenance_menu.addAction("Проверить и восстановить")
        self.sync_action.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self.sync_action.triggered.connect(self.synchronize)
        self.maintenance_button.setMenu(self.maintenance_menu)
        heading.addWidget(self.maintenance_button)
        self.trash_button = self.trash_action
        self.health_button = self.health_action
        self.sync_button = self.sync_action
''',
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''        list_layout.addLayout(paging)
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
''',
    '''        list_layout.addLayout(paging)

        self.content_splitter = QSplitter(Qt.Horizontal)
        self.content_splitter.setObjectName("materialsSplitView")
        self.content_splitter.setAccessibleName("Список и содержимое материалов")
        self.content_splitter.addWidget(list_panel)

        details_scroll = QScrollArea()
        details_scroll.setObjectName("materialsDetailsScroll")
        details_scroll.setWidgetResizable(True)
        details = QFrame()
''',
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''        details_title = QLabel("Содержимое занятия")
        details_title.setObjectName("tileTitle")
        details_header.addWidget(details_title, 1)
''',
    '''        self.details_title = QLabel("Выберите занятие")
        self.details_title.setObjectName("tileTitle")
        self.details_title.setWordWrap(True)
        details_header.addWidget(self.details_title, 1)
''',
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''        self.close_details_button = set_button_kind(QPushButton("Закрыть карточку"), "ghost")
        self.close_details_button.clicked.connect(self.details_dialog.close)
        details_header.addWidget(self.close_details_button)
        details_layout.addLayout(details_header)
''',
    '''        details_layout.addLayout(details_header)
''',
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''        dialog_layout.addWidget(details)
        self._clear_details()
''',
    '''        details_scroll.setWidget(details)
        self.content_splitter.addWidget(details_scroll)
        self.content_splitter.setSizes([660, 560])
        self.content_splitter.setStretchFactor(0, 3)
        self.content_splitter.setStretchFactor(1, 2)
        layout.addWidget(self.content_splitter, 1)
        self._clear_details()
''',
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''        self.close_details_button.setEnabled(False)
        self.details_dialog.set_close_allowed(False)
        self.transcript.setReadOnly(False)
''',
    '''        self.transcript.setReadOnly(False)
''',
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''        self.table.setEnabled(True)
        self.close_details_button.setEnabled(True)
        self.details_dialog.set_close_allowed(True)
        self.delete_lesson_button.setEnabled(self._current_content is not None)
''',
    '''        self.table.setEnabled(True)
        self.delete_lesson_button.setEnabled(self._current_content is not None)
''',
)
replace_re(
    "src/tutor_assistant/ui/student_content.py",
    r'''        details_visible = self\.details_dialog\.isVisible\(\)
        if selected_row >= 0 and details_visible:
            self\.table\.selectRow\(selected_row\)
        else:
            self\.table\.clearSelection\(\)
        self\.table\.blockSignals\(False\)
        if selected_row >= 0 and details_visible:
            self\._load_selected\(activate=False\)
        elif not self\._transcript_editing:
            if details_visible:
                self\.details_dialog\.close\(\)
            self\._selected_lesson_id = None
            self\._clear_details\(\)
''',
    '''        if selected_row >= 0:
            self.table.selectRow(selected_row)
        else:
            self.table.clearSelection()
        self.table.blockSignals(False)
        if selected_row >= 0:
            self._load_selected(activate=False)
        elif not self._transcript_editing:
            self._selected_lesson_id = None
            self._clear_details()
''',
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''        self._selected_lesson_id = lesson_id
        self.details_dialog.setWindowTitle(f"Содержимое занятия · {lesson_id}")
        if not self.details_dialog.isVisible():
            self.details_dialog.open()
        if activate:
            self.details_dialog.raise_()
            self.details_dialog.activateWindow()
        self._detail_request += 1
''',
    '''        self._selected_lesson_id = lesson_id
        self.details_title.setText(f"Загружаю занятие · {lesson_id}")
        self._detail_request += 1
''',
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''        lesson = result.lesson
        self.details_dialog.setWindowTitle(f"Содержимое занятия · {lesson.topic}")
        self.metadata["student"].setText(lesson.student.full_name)
''',
    '''        lesson = result.lesson
        self.details_title.setText(lesson.topic or "Содержимое занятия")
        self.metadata["student"].setText(lesson.student.full_name)
''',
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''                else "Для занятия нет проиндексированного транскрипта"
            )

    def _detail_failed(self, request_id: int, details: str) -> None:
''',
    '''                else "Для занятия нет проиндексированного транскрипта"
            )
        self.content_changed.emit()

    def _detail_failed(self, request_id: int, details: str) -> None:
''',
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''        self._current_content = None
        for label in getattr(self, "metadata", {}).values():
''',
    '''        self._current_content = None
        if hasattr(self, "details_title"):
            self.details_title.setText("Выберите занятие")
        for label in getattr(self, "metadata", {}).values():
''',
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '''            if button is not None:
                button.setEnabled(False)

    def close_details(self) -> None:
        self.details_dialog.close()

    def _details_dialog_closed(self, _result: int) -> None:
        self.playback_panel.stop(clear_source=True)
        self.table.clearSelection()
        self._selected_lesson_id = None
        self._detail_request += 1
        self._clear_details()
''',
    '''            if button is not None:
                button.setEnabled(False)
        self.content_changed.emit()

    def close_details(self) -> None:
        if self._transcript_editing:
            self.status_changed.emit(
                "Завершите редактирование транскрипта перед очисткой карточки",
                "warning",
            )
            return
        self.playback_panel.stop(clear_source=True)
        self.table.clearSelection()
        self._selected_lesson_id = None
        self._detail_request += 1
        self._clear_details()
''',
)
replace_once(
    "src/tutor_assistant/ui/library_transcription.py",
    '''    page.files_table.itemSelectionChanged.connect(sync)
    page.details_dialog.finished.connect(lambda _result: sync())
    button.clicked.connect(request)
''',
    '''    page.files_table.itemSelectionChanged.connect(sync)
    content_changed = getattr(page, "content_changed", None)
    if content_changed is not None:
        content_changed.connect(sync)
    elif hasattr(page, "details_dialog"):
        page.details_dialog.finished.connect(lambda _result: sync())
    button.clicked.connect(request)
''',
)

# ---------------------------------------------------------------------------
# Version and documentation.
# ---------------------------------------------------------------------------
replace_once(
    "pyproject.toml",
    'version = "0.20.0"',
    'version = "0.21.0"',
)
replace_once(
    "src/tutor_assistant/__init__.py",
    '__version__ = "0.20.0"',
    '__version__ = "0.21.0"',
)
replace_once(
    "README.md",
    "## ",
    dedent(
        '''\
        ## UX-3: CRM и материалы

        - Карточка ученика показывает dirty-state и защищает изменения при переключении.
        - Учебные поля отделены от раскрываемых технических параметров.
        - Материалы работают в split view со списком и постоянной панелью содержимого.
        - Корзина, диагностика и восстановление собраны в меню обслуживания архива.
        - Расписание использует 30-минутную сетку и отображает длительность занятия высотой блока.

        ## '''
    ),
)

# ---------------------------------------------------------------------------
# GUI contracts.
# ---------------------------------------------------------------------------
replace_once(
    "tests/test_student_content_gui.py",
    '''    assert page.table.rowCount() == 1
    assert not page.details_dialog.isVisible()

    page.table.selectRow(0)
    application.processEvents()
    assert page.details_dialog.isVisible()
    assert page.details_dialog.accessibleName() == "Содержимое занятия"
    assert page.metadata["topic"].text() == "GUI hardening"
    page.close_details()
    application.processEvents()
''',
    '''    assert page.table.rowCount() == 1
    assert page.content_splitter.accessibleName() == "Список и содержимое материалов"
    assert page.details_title.text() == "Выберите занятие"

    page.table.selectRow(0)
    application.processEvents()
    assert page.metadata["topic"].text() == "GUI hardening"
    assert page.details_title.text() == "GUI hardening"
    page.close_details()
    application.processEvents()
    assert page.details_title.text() == "Выберите занятие"
''',
)

TESTS = dedent(
    '''
    from __future__ import annotations

    from datetime import date, datetime
    from pathlib import Path

    import pytest

    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

    from PySide6.QtWidgets import QApplication, QMessageBox

    from tutor_assistant.content import StudentContentService
    from tutor_assistant.crm import CrmStore, ScheduledLesson, StudentProfile
    from tutor_assistant.domain import Lesson, Student
    from tutor_assistant.playback import PlaybackController
    from tutor_assistant.ui.crm import SchedulePage, StudentsPage
    from tutor_assistant.ui.student_content import StudentContentPage


    class TestCodec:
        def encrypt(self, value: str | None) -> str | None:
            return value

        def decrypt(self, value: str | None) -> str | None:
            return value


    class FakePlaybackBackend:
        def load(self, _path: Path) -> None:
            return

        def play(self) -> None:
            return

        def pause(self) -> None:
            return

        def stop(self) -> None:
            return

        def set_position(self, _position_ms: int) -> None:
            return

        def position_ms(self) -> int:
            return 0

        def set_rate(self, _rate: float) -> None:
            return

        def is_playing(self) -> bool:
            return False


    class FakeScheduler:
        def schedule(self, _delay_ms: int, _callback) -> None:
            return

        def cancel(self) -> None:
            return


    @pytest.fixture(scope="module")
    def application() -> QApplication:
        return QApplication.instance() or QApplication([])


    def test_student_card_dirty_state_and_field_hierarchy(
        tmp_path: Path,
        application: QApplication,
        monkeypatch,
    ) -> None:
        store = CrmStore(tmp_path / "crm.sqlite3", TestCodec())
        store.save_student(StudentProfile(id="anna", full_name="Анна"), [])
        store.save_student(StudentProfile(id="boris", full_name="Борис"), [])
        page = StudentsPage(store)
        page.show()
        page.table.selectRow(0)
        application.processEvents()

        assert page.technical_panel.isHidden()
        assert page.dirty_label.text() == "Все изменения сохранены"
        page.full_name.setText("Анна Петрова")
        page.full_name.textEdited.emit("Анна Петрова")
        assert page._dirty
        assert page.save_button.isEnabled()
        assert page.dirty_label.text() == "Есть несохранённые изменения"

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.Cancel,
        )
        page.table.selectRow(1)
        application.processEvents()
        assert page.current_id == "anna"
        assert page._dirty

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.Discard,
        )
        page.table.selectRow(1)
        application.processEvents()
        assert page.current_id == "boris"
        assert not page._dirty

        page.technical_toggle.setChecked(True)
        assert not page.technical_panel.isHidden()
        page.close()


    def test_materials_are_embedded_in_split_view_with_maintenance_menu(
        tmp_path: Path,
        application: QApplication,
    ) -> None:
        service = StudentContentService(tmp_path / "data")
        student = Student(id="student", full_name="Ученик")
        service.create_lesson(
            Lesson(
                lesson_id="ux3-material",
                student=student,
                subject="mathematics",
                lesson_date=date(2026, 8, 1),
                topic="Split view",
            )
        )
        backend = FakePlaybackBackend()
        controller = PlaybackController(backend, FakeScheduler(), lambda: True)

        def run_background(callable_, succeeded, failed) -> None:
            try:
                succeeded(callable_())
            except Exception as exc:
                failed(str(exc))

        page = StudentContentPage(service, [student], run_background, controller, backend)
        page.ensure_loaded()
        page.show()
        application.processEvents()

        assert page.content_splitter.count() == 2
        assert page.maintenance_button.menu() is page.maintenance_menu
        assert [action.text() for action in page.maintenance_menu.actions() if action.text()] == [
            "Корзина",
            "Диагностика архива",
            "Проверить и восстановить",
        ]
        page.table.selectRow(0)
        application.processEvents()
        assert page.details_title.text() == "Split view"
        assert page.metadata["topic"].text() == "Split view"
        page.close()


    def test_schedule_uses_half_hour_rows_and_duration_spans(
        tmp_path: Path,
        application: QApplication,
    ) -> None:
        store = CrmStore(tmp_path / "schedule.sqlite3", TestCodec())
        store.save_student(StudentProfile(id="student", full_name="Ученик"), [])
        week_start = date(2026, 7, 27)
        store.save_one_off(
            ScheduledLesson(
                student_id="student",
                student_name="Ученик",
                starts_at=datetime(2026, 7, 27, 16, 30),
                duration_minutes=90,
                subject="mathematics",
                topic="Полуторачасовое занятие",
            )
        )
        page = SchedulePage(store)
        page.week_start = week_start
        page.refresh()
        page.show()
        application.processEvents()

        assert page.grid.rowCount() == 32
        row = page._row_for_time(16, 30)
        assert page.grid.verticalHeaderItem(row).text() == "16:30"
        assert page.grid.rowSpan(row, 0) == 3
        assert page.cell_lessons[(row + 2, 0)].student_id == "student"
        page.grid.setCurrentCell(row + 1, 0)
        assert page.open_selected_button.text() == "Открыть занятие"
        page.close()
    '''
).lstrip()
write("tests/test_ux3_crm_materials_gui.py", TESTS)

print("UX-3 CRM and materials patch applied")
