from __future__ import annotations

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
        raise RuntimeError(f"{path}: expected one marker, found {count}: {old[:80]!r}")
    write(path, content.replace(old, new, 1))


replace_once(
    "src/tutor_assistant/ui/app.py",
    "from .localization import select_subject, set_subject_combo, subject_value\n",
    "from .localization import select_subject, set_subject_combo, subject_label, subject_value\n",
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    '''        self.quick_readiness_button = QPushButton("✓")
        self.quick_readiness_button.setObjectName("quickStatusButton")
        self.quick_readiness_button.clicked.connect(self._show_readiness_dialog)
        top_row.addWidget(self.quick_readiness_button)

        self.quick_options_button = QPushButton("···")
        self.quick_options_button.setObjectName("quickIconButton")
        self.quick_options_button.setToolTip("Профиль и предмет")
        self.quick_options_button.clicked.connect(self._show_quick_options_dialog)
        top_row.addWidget(self.quick_options_button)
        surface_layout.addLayout(top_row)
        surface_layout.addSpacing(2)
        surface_layout.addWidget(self.quick_student)
        surface_layout.addWidget(self.quick_topic)
''',
    '''        self.quick_readiness_button = QPushButton("Проверить")
        self.quick_readiness_button.setObjectName("quickStatusButton")
        self.quick_readiness_button.setAccessibleName("Открыть подробную проверку готовности")
        self.quick_readiness_button.clicked.connect(self._show_readiness_dialog)
        top_row.addWidget(self.quick_readiness_button)

        self.quick_options_button = QPushButton("···")
        self.quick_options_button.setObjectName("quickIconButton")
        self.quick_options_button.setToolTip("Изменить профиль и предмет")
        self.quick_options_button.clicked.connect(self._show_quick_options_dialog)
        top_row.addWidget(self.quick_options_button)
        surface_layout.addLayout(top_row)

        quick_context = QFrame()
        quick_context.setObjectName("infoPanel")
        quick_context_layout = QHBoxLayout(quick_context)
        quick_context_layout.setContentsMargins(12, 9, 12, 9)
        quick_context_layout.setSpacing(10)
        self.quick_profile_text = QLabel()
        self.quick_profile_text.setObjectName("muted")
        self.quick_subject_text = QLabel()
        self.quick_subject_text.setObjectName("muted")
        quick_context_layout.addWidget(self.quick_profile_text)
        quick_context_layout.addWidget(self.quick_subject_text)
        quick_context_layout.addStretch(1)
        surface_layout.addWidget(quick_context)

        self.quick_readiness_text = QLabel()
        self.quick_readiness_text.setObjectName("readinessSummary")
        self.quick_readiness_text.setWordWrap(True)
        self.quick_readiness_text.setAccessibleName("Состояние готовности быстрого урока")
        surface_layout.addWidget(self.quick_readiness_text)
        surface_layout.addWidget(self.quick_student)
        surface_layout.addWidget(self.quick_topic)
''',
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    '''        self.quick_readiness_button.setText("✓" if readiness.ready else "!")
        self.quick_readiness_button.setProperty("tone", "ready" if readiness.ready else "blocked")
        lines = [f"{'✓' if item.ready else '!'} {item.label}: {item.detail}" for item in readiness.items]
        lines.append("")
        lines.append("Нажмите, чтобы открыть подробную проверку")
        self.quick_readiness_button.setToolTip("\\n".join(lines))
        refresh_style(self.quick_readiness_button)
''',
    '''        profile = selected_profile(self.config, self.quick_profile.currentData())
        self.quick_profile_text.setText(f"Профиль: {profile.name}")
        self.quick_subject_text.setText(f"Предмет: {self.quick_subject.currentText()}")
        self.quick_readiness_button.setText("Проверить")
        self.quick_readiness_button.setProperty("tone", "ready" if readiness.ready else "blocked")
        if readiness.ready:
            readiness_text = "Готово к старту · данные урока и аудио проверены"
        else:
            blocker = readiness.blockers[0].detail if readiness.blockers else "Проверьте параметры урока"
            readiness_text = f"Требуется действие · {blocker}"
        self.quick_readiness_text.setText(readiness_text)
        self.quick_readiness_text.setProperty("tone", "ready" if readiness.ready else "blocked")
        lines = [f"{'✓' if item.ready else '!'} {item.label}: {item.detail}" for item in readiness.items]
        lines.append("")
        lines.append("Нажмите, чтобы открыть подробную проверку")
        self.quick_readiness_button.setToolTip("\\n".join(lines))
        refresh_style(self.quick_readiness_button)
        refresh_style(self.quick_readiness_text)
''',
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    '''        self.approve.setShortcut(QKeySequence("Ctrl+Return"))
        self.approve.setToolTip("Подтвердить транскрипт · Ctrl+Enter")
''',
    '''        self.approve.setShortcut(QKeySequence("Ctrl+Return"))
        self.approve.setToolTip("Подтвердить транскрипт и перейти к публикации · Ctrl+Enter")
''',
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    '''        workspace.primary_action_button.clicked.connect(
            self._handle_transcript_primary_action
        )
        workspace.open_review_action.triggered.connect(self.open_normalization_result)
''',
    '''        workspace.primary_action_button.clicked.connect(
            self._handle_transcript_primary_action
        )
        workspace.review_result_button.clicked.connect(self.open_normalization_result)
''',
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    '''        self.processing_list.setAlternatingRowColors(True)
        self.processing_list.setSpacing(3)
        self.processing_list.itemDoubleClicked.connect(self._open_processing_item)
        layout.addWidget(self.processing_list, 1)
        hint = QLabel("Двойной клик по готовому заданию открывает транскрипт для проверки")
        hint.setObjectName("muted")
        layout.addWidget(hint)
''',
    '''        self.processing_list.setAlternatingRowColors(True)
        self.processing_list.setSpacing(3)
        self.processing_list.itemSelectionChanged.connect(self._sync_processing_actions)
        self.processing_list.itemDoubleClicked.connect(self._open_processing_item)
        layout.addWidget(self.processing_list, 1)
        processing_actions = QHBoxLayout()
        hint = QLabel("Выберите задание, затем откройте готовый транскрипт или повторите ошибку")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        processing_actions.addWidget(hint, 1)
        self.processing_open_button = set_button_kind(
            QPushButton("Открыть выбранное"),
            "primary",
        )
        self.processing_open_button.setEnabled(False)
        self.processing_open_button.clicked.connect(self._open_selected_processing_item)
        processing_actions.addWidget(self.processing_open_button)
        layout.addLayout(processing_actions)
''',
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    '''        preview_box = QGroupBox("Предпросмотр страниц")
        preview_layout = QVBoxLayout(preview_box)
        preview_hint = QLabel("Двойной клик открывает страницу")
        preview_hint.setObjectName("muted")
        preview_layout.addWidget(preview_hint)
        self.pdf_previews = QListWidget()
        self.pdf_previews.itemDoubleClicked.connect(
            lambda item: QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(256)))
        )
        preview_layout.addWidget(self.pdf_previews)
''',
    '''        preview_box = QGroupBox("Предпросмотр страниц")
        preview_layout = QVBoxLayout(preview_box)
        preview_actions = QHBoxLayout()
        preview_hint = QLabel("Выберите страницу для открытия в системном просмотрщике")
        preview_hint.setObjectName("muted")
        preview_hint.setWordWrap(True)
        preview_actions.addWidget(preview_hint, 1)
        self.open_pdf_preview_button = set_button_kind(
            QPushButton("Открыть страницу"),
            "ghost",
        )
        self.open_pdf_preview_button.setEnabled(False)
        self.open_pdf_preview_button.clicked.connect(self._open_selected_pdf_preview)
        preview_actions.addWidget(self.open_pdf_preview_button)
        preview_layout.addLayout(preview_actions)
        self.pdf_previews = QListWidget()
        self.pdf_previews.itemSelectionChanged.connect(self._sync_pdf_preview_action)
        self.pdf_previews.itemDoubleClicked.connect(
            lambda _item: self._open_selected_pdf_preview()
        )
        preview_layout.addWidget(self.pdf_previews)
''',
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    '''    def _show_processing_queue(self) -> None:
        self._set_mode("detailed")
        self.tabs.setCurrentIndex(4)

    def _open_processing_item(self, item: QListWidgetItem) -> None:
''',
    '''    def _show_processing_queue(self) -> None:
        self._set_mode("detailed")
        self.tabs.setCurrentIndex(4)

    def _sync_processing_actions(self) -> None:
        if hasattr(self, "processing_open_button"):
            self.processing_open_button.setEnabled(self.processing_list.currentItem() is not None)

    def _open_selected_processing_item(self) -> None:
        item = self.processing_list.currentItem()
        if item is not None:
            self._open_processing_item(item)

    def _open_processing_item(self, item: QListWidgetItem) -> None:
''',
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    '''        results.addWidget(preview_box, 2)
        layout.addLayout(results, 1)
        return page

    def _make_lesson(self) -> Lesson:
''',
    '''        results.addWidget(preview_box, 2)
        layout.addLayout(results, 1)
        return page

    def _sync_pdf_preview_action(self) -> None:
        if hasattr(self, "open_pdf_preview_button"):
            self.open_pdf_preview_button.setEnabled(self.pdf_previews.currentItem() is not None)

    def _open_selected_pdf_preview(self) -> None:
        item = self.pdf_previews.currentItem()
        if item is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(item.data(256))))

    def _make_lesson(self) -> Lesson:
''',
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    '''        self.transcript_workspace.set_menu_state(
            open_result=artifact_ready,
            restart=can_start and run is not None,
            open_artifact=artifact_ready,
            show_warnings=artifact_ready,
            reject=reject_enabled,
        )
''',
    '''        self.transcript_workspace.set_review_action(visible=False, enabled=False)
        self.transcript_workspace.set_menu_state(
            restart=can_start and run is not None,
            open_artifact=artifact_ready,
            show_warnings=artifact_ready,
            reject=reject_enabled,
        )
''',
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    '''        if run and run.status == NormalizationRunStatus.REVIEW_REQUIRED and artifact_ready:
            self._transcript_primary_action = "review"
            self.transcript_workspace.set_primary_action(
                "Проверить результат",
                enabled=True,
            )
''',
    '''        if run and run.status == NormalizationRunStatus.REVIEW_REQUIRED and artifact_ready:
            self._transcript_primary_action = "start"
            self.transcript_workspace.set_primary_action(
                "Запустить фильтрацию",
                enabled=False,
                visible=False,
            )
            self.transcript_workspace.set_review_action(
                visible=True,
                enabled=True,
                text="Проверить результат перед применением",
            )
''',
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    '''            self._sync_normalization_controls()
            self.transcript_workspace.select_summary()
        self._set_status("LLM-фильтрация применена как новая ревизия")
''',
    '''            self._sync_normalization_controls()
            self.transcript_workspace.select_summary()
            self._go_to(2)
        self._set_status("Результат проверен · транскрипт готов к публикации")
''',
)

replace_once(
    "src/tutor_assistant/ui/transcript_workspace.py",
    '''        summary_hint = QLabel("Подтверждённая версия будет опубликована в папке ученика")
''',
    '''        summary_hint = QLabel("Финальный шаг: подтвердите текст и перейдите к публикации")
''',
)
replace_once(
    "src/tutor_assistant/ui/transcript_workspace.py",
    '''            QPushButton("Подтвердить транскрипт"),
''',
    '''            QPushButton("Подтвердить и перейти к публикации"),
''',
)
replace_once(
    "src/tutor_assistant/ui/transcript_workspace.py",
    '''        self.overflow_menu = QMenu(self.overflow_button)
        self.open_review_action = self.overflow_menu.addAction("Проверить результат")
        self.restart_action = self.overflow_menu.addAction("Запустить фильтрацию заново")
''',
    '''        self.overflow_menu = QMenu(self.overflow_button)
        self.restart_action = self.overflow_menu.addAction("Запустить фильтрацию заново")
''',
)
replace_once(
    "src/tutor_assistant/ui/transcript_workspace.py",
    '''        state_row.addLayout(state_text, 1)
        self.primary_action_button = set_button_kind(
            QPushButton("Запустить фильтрацию"),
            "primary",
        )
''',
    '''        state_row.addLayout(state_text, 1)
        self.review_result_button = set_button_kind(
            QPushButton("Проверить результат перед применением"),
            "primary",
        )
        self.review_result_button.setObjectName("normalizationReviewAction")
        self.review_result_button.setAccessibleName("Обязательная проверка результата LLM")
        self.review_result_button.setVisible(False)
        state_row.addWidget(self.review_result_button, 0, Qt.AlignBottom)
        self.primary_action_button = set_button_kind(
            QPushButton("Запустить фильтрацию"),
            "primary",
        )
''',
)
replace_once(
    "src/tutor_assistant/ui/transcript_workspace.py",
    '''    def set_primary_action(self, text: str, *, enabled: bool, kind: str = "primary") -> None:
        self.primary_action_button.setText(text)
        self.primary_action_button.setEnabled(enabled)
        self.primary_action_button.setProperty("kind", kind)
        refresh_style(self.primary_action_button)
''',
    '''    def set_primary_action(
        self,
        text: str,
        *,
        enabled: bool,
        kind: str = "primary",
        visible: bool = True,
    ) -> None:
        self.primary_action_button.setText(text)
        self.primary_action_button.setEnabled(enabled)
        self.primary_action_button.setVisible(visible)
        self.primary_action_button.setProperty("kind", kind)
        refresh_style(self.primary_action_button)

    def set_review_action(
        self,
        *,
        visible: bool,
        enabled: bool,
        text: str = "Проверить результат перед применением",
    ) -> None:
        self.review_result_button.setText(text)
        self.review_result_button.setEnabled(enabled)
        self.review_result_button.setVisible(visible)
''',
)
replace_once(
    "src/tutor_assistant/ui/transcript_workspace.py",
    '''        self.process_card.setProperty("tone", tone)
        self.progress.setVisible(show_progress)
        refresh_style(self.process_card)
''',
    '''        self.process_card.setProperty("tone", tone)
        self.progress.setVisible(show_progress)
        if not show_progress:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
        refresh_style(self.process_card)
''',
)
replace_once(
    "src/tutor_assistant/ui/transcript_workspace.py",
    '''    def set_menu_state(
        self,
        *,
        open_result: bool,
        restart: bool,
        open_artifact: bool,
        show_warnings: bool,
        reject: bool,
    ) -> None:
        self.open_review_action.setEnabled(open_result)
        self.restart_action.setEnabled(restart)
''',
    '''    def set_menu_state(
        self,
        *,
        restart: bool,
        open_artifact: bool,
        show_warnings: bool,
        reject: bool,
    ) -> None:
        self.restart_action.setEnabled(restart)
''',
)

replace_once(
    "src/tutor_assistant/ui/normalization.py",
    '''            "Применить как новую ревизию",
''',
    '''            "Применить и перейти к публикации",
''',
)
replace_once(
    "src/tutor_assistant/ui/normalization.py",
    '''        apply_button.setToolTip("Перед применением можно отредактировать отфильтрованный текст")
''',
    '''        apply_button.setToolTip(
            "Перед применением можно отредактировать текст; после подтверждения откроется публикация"
        )
''',
)

replace_once(
    "src/tutor_assistant/ui/content_import.py",
    '''        self.state.setText(details)
        self.state.setStyleSheet("color: #A33636;")
''',
    '''        self.progress.setVisible(False)
        self.progress.setValue(0)
        self.state.setText(details)
        self.state.setStyleSheet("color: #A33636;")
''',
)

replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        add_guardian = set_button_kind(QPushButton("Добавить"), "ghost")
        add_guardian.clicked.connect(self._add_guardian)
        guardian_header.addWidget(add_guardian)
        remove_guardian = set_button_kind(QPushButton("Удалить"), "ghost")
''',
    '''        add_guardian = set_button_kind(QPushButton("Добавить"), "ghost")
        add_guardian.clicked.connect(self._add_guardian)
        guardian_header.addWidget(add_guardian)
        self.edit_guardian_button = set_button_kind(QPushButton("Изменить"), "ghost")
        self.edit_guardian_button.setEnabled(False)
        self.edit_guardian_button.clicked.connect(self._edit_guardian)
        guardian_header.addWidget(self.edit_guardian_button)
        remove_guardian = set_button_kind(QPushButton("Удалить"), "ghost")
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        self.guardian_table.verticalHeader().setVisible(False)
        self.guardian_table.doubleClicked.connect(self._edit_guardian)
''',
    '''        self.guardian_table.verticalHeader().setVisible(False)
        self.guardian_table.itemSelectionChanged.connect(self._sync_guardian_actions)
        self.guardian_table.doubleClicked.connect(self._edit_guardian)
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''    def _add_guardian(self) -> None:
''',
    '''    def _sync_guardian_actions(self) -> None:
        self.edit_guardian_button.setEnabled(self.guardian_table.currentRow() >= 0)

    def _add_guardian(self) -> None:
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''            for column, value in enumerate(values):
                self.guardian_table.setItem(row, column, QTableWidgetItem(value))

    def _sync_guardian_actions(self) -> None:
''',
    '''            for column, value in enumerate(values):
                self.guardian_table.setItem(row, column, QTableWidgetItem(value))
        self._sync_guardian_actions()

    def _sync_guardian_actions(self) -> None:
''',
)

replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        add = set_button_kind(QPushButton("Добавить занятие"), "primary")
        add.clicked.connect(lambda: self._open_dialog(self.week_start, 16))
        header.addWidget(add)
        layout.addLayout(header)
''',
    '''        add = set_button_kind(QPushButton("Добавить занятие"), "primary")
        add.clicked.connect(lambda: self._open_dialog(self.week_start, 16))
        header.addWidget(add)
        self.open_selected_button = set_button_kind(
            QPushButton("Открыть выбранное"),
            "ghost",
        )
        self.open_selected_button.setEnabled(False)
        self.open_selected_button.clicked.connect(self._open_selected_cell)
        header.addWidget(self.open_selected_button)
        layout.addLayout(header)
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        self.grid.setEditTriggers(QTableWidget.NoEditTriggers)
        self.grid.setShowGrid(False)
        self.grid.cellDoubleClicked.connect(self._cell_opened)
''',
    '''        self.grid.setEditTriggers(QTableWidget.NoEditTriggers)
        self.grid.setShowGrid(False)
        self.grid.currentCellChanged.connect(self._sync_schedule_action)
        self.grid.cellDoubleClicked.connect(self._cell_opened)
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        self.week_label.setText(
            f"{self.week_start:%d.%m.%Y} — {end:%d.%m.%Y} · двойной клик открывает занятие"
        )
''',
    '''        self.week_label.setText(
            f"{self.week_start:%d.%m.%Y} — {end:%d.%m.%Y} · выберите ячейку для действия"
        )
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''                f"–{lesson.ends_at:%H:%M}\\n{lesson.topic or lesson.subject}\\n"
                "Двойной клик — открыть"
''',
    '''                f"–{lesson.ends_at:%H:%M}\\n{lesson.topic or lesson.subject}\\n"
                "Выберите ячейку и нажмите «Открыть выбранное»"
''',
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '''        self.revenue_stat.setText(f"План · {stats.planned_revenue_cents / 100:,.0f} ₽")

    def _shift_week(self, days: int) -> None:
''',
    '''        self.revenue_stat.setText(f"План · {stats.planned_revenue_cents / 100:,.0f} ₽")
        self._sync_schedule_action()

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
''',
)

replace_once(
    "src/tutor_assistant/__init__.py",
    '__version__ = "0.19.0"',
    '__version__ = "0.20.0"',
)
replace_once(
    "pyproject.toml",
    'version = "0.19.0"',
    'version = "0.20.0"',
)

readme = read("README.md")
marker = "## UX-2: основные рабочие сценарии"
if marker not in readme:
    readme += dedent(
        '''

        ## UX-2: основные рабочие сценарии

        Версия 0.20.0 делает ключевые действия видимыми в самих рабочих областях:

        - быстрый режим постоянно показывает профиль, предмет и текст готовности;
        - подтверждение транскрипта и применение проверенного LLM-результата ведут к публикации;
        - обязательная проверка результата вынесена из меню в отдельное действие;
        - индикаторы прогресса скрываются после завершения или ошибки;
        - очередь, расписание, представители и PDF-превью получили явные кнопки действий.
        '''
    )
    write("README.md", readme)

TESTS = dedent(
    r'''
    from __future__ import annotations

    import inspect

    from PySide6.QtWidgets import QApplication

    from tutor_assistant.ui import app as app_module
    from tutor_assistant.ui.content_import import ImportLessonDialog
    from tutor_assistant.ui.crm import SchedulePage, StudentsPage
    from tutor_assistant.ui.normalization import ContentFilterReviewDialog
    from tutor_assistant.ui.transcript_workspace import TranscriptWorkspace

    _APPLICATION: QApplication | None = None


    def _application() -> QApplication:
        global _APPLICATION
        existing = QApplication.instance()
        if isinstance(existing, QApplication):
            _APPLICATION = existing
        elif _APPLICATION is None:
            _APPLICATION = QApplication([])
        return _APPLICATION


    def test_quick_mode_exposes_profile_subject_and_readiness_text() -> None:
        source = inspect.getsource(app_module.MainWindow._quick_start_page)
        refresh_source = inspect.getsource(app_module.MainWindow._refresh_quick_readiness)

        assert "quick_profile_text" in source
        assert "quick_subject_text" in source
        assert "quick_readiness_text" in source
        assert "Профиль:" in refresh_source
        assert "Предмет:" in refresh_source
        assert "Готово к старту" in refresh_source


    def test_transcript_workspace_has_one_explicit_final_action() -> None:
        _application()
        workspace = TranscriptWorkspace()

        assert workspace.approve_button.text() == "Подтвердить и перейти к публикации"
        assert workspace.review_result_button.isHidden()
        assert not hasattr(workspace, "open_review_action")

        workspace.set_review_action(visible=True, enabled=True)
        workspace.set_primary_action("Запустить фильтрацию", enabled=False, visible=False)

        assert workspace.review_result_button.isVisible()
        assert workspace.review_result_button.isEnabled()
        assert workspace.primary_action_button.isHidden()


    def test_idle_progress_bars_are_hidden() -> None:
        _application()
        workspace = TranscriptWorkspace()
        assert workspace.progress.isHidden()

        workspace.set_progress(total=4, completed=1, title="Выполняется", detail="Блок 1")
        assert workspace.progress.isVisible()
        workspace.set_process_state("Готово", "Операция завершена", tone="success")
        assert workspace.progress.isHidden()
        assert workspace.progress.value() == 0

        dialog = ImportLessonDialog([])
        dialog.set_running()
        assert dialog.progress.isVisible()
        dialog.show_error("Ошибка импорта")
        assert dialog.progress.isHidden()


    def test_required_review_and_explicit_actions_are_first_class_controls() -> None:
        app_source = inspect.getsource(app_module.MainWindow)
        students_source = inspect.getsource(StudentsPage._build)
        schedule_source = inspect.getsource(SchedulePage._build)
        review_source = inspect.getsource(ContentFilterReviewDialog.__init__)

        assert "processing_open_button" in app_source
        assert "open_pdf_preview_button" in app_source
        assert "review_result_button.clicked.connect" in app_source
        assert "edit_guardian_button" in students_source
        assert "open_selected_button" in schedule_source
        assert "Применить и перейти к публикации" in review_source
    '''
).lstrip()
write("tests/test_ux2_core_workflows_gui.py", TESTS)

print("UX-2 core workflows patch applied")
