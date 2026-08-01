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
        raise RuntimeError(f"Expected one occurrence in {path}, found {count}: {old[:120]!r}")
    write(path, content.replace(old, new, 1))


def replace_all(path: str, old: str, new: str, *, minimum: int = 1) -> None:
    content = read(path)
    count = content.count(old)
    if count < minimum:
        raise RuntimeError(f"Expected at least {minimum} occurrences in {path}, found {count}: {old[:120]!r}")
    write(path, content.replace(old, new))


LOCALIZATION = dedent(
    '''
    from __future__ import annotations

    from collections.abc import Iterable

    from PySide6.QtWidgets import QComboBox

    SUBJECT_LABELS: dict[str, str] = {
        "mathematics": "Математика",
        "physics": "Физика",
        "chemistry": "Химия",
    }

    CONTACT_CHANNEL_LABELS: dict[str, str] = {
        "phone": "Телефон",
        "email": "Электронная почта",
        "social": "Социальная сеть",
    }

    _SUBJECT_ALIASES = {
        alias.casefold(): value
        for value, label in SUBJECT_LABELS.items()
        for alias in (value, label)
    }


    def subject_label(value: str | None) -> str:
        normalized = (value or "").strip()
        return SUBJECT_LABELS.get(normalized.casefold(), normalized)


    def subject_value(value: str | None) -> str:
        normalized = (value or "").strip()
        return _SUBJECT_ALIASES.get(normalized.casefold(), normalized)


    def subject_items(values: Iterable[str] | None = None) -> tuple[tuple[str, str], ...]:
        source = tuple(values) if values is not None else tuple(SUBJECT_LABELS)
        return tuple((subject_label(value), subject_value(value)) for value in source)


    def set_subject_combo(
        combo: QComboBox,
        values: Iterable[str] | None = None,
        *,
        selected: str | None = None,
    ) -> None:
        current = subject_value(selected or combo.currentData() or combo.currentText())
        editable = combo.isEditable()
        combo.blockSignals(True)
        combo.clear()
        for label, value in subject_items(values):
            combo.addItem(label, value)
        combo.setEditable(editable)
        combo.blockSignals(False)
        select_subject(combo, current)


    def select_subject(combo: QComboBox, value: str | None) -> bool:
        canonical = subject_value(value)
        index = combo.findData(canonical)
        if index >= 0:
            combo.setCurrentIndex(index)
            return True
        if combo.isEditable() and canonical:
            combo.setCurrentText(subject_label(canonical))
            return True
        return False


    def subject_list_text(values: Iterable[str]) -> str:
        return ", ".join(subject_label(value) for value in values)


    def parse_subject_list(value: str) -> list[str]:
        result: list[str] = []
        for item in value.split(","):
            canonical = subject_value(item)
            if canonical and canonical not in result:
                result.append(canonical)
        return result


    def contact_channel_label(value: str | None) -> str:
        normalized = (value or "").strip()
        return CONTACT_CHANNEL_LABELS.get(normalized.casefold(), normalized)
    '''
).lstrip()


INFORMATION_ARCHITECTURE = dedent(
    '''
    from __future__ import annotations

    from dataclasses import dataclass

    from PySide6.QtCore import Qt, Signal
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    from .theme import refresh_style


    @dataclass(frozen=True, slots=True)
    class NavigationEntry:
        group: str
        label: str
        page_index: int | None
        accessible_name: str


    NAVIGATION_ENTRIES = (
        NavigationEntry("РАБОТА", "Быстрый урок", None, "Открыть быстрый запуск занятия"),
        NavigationEntry("РАБОТА", "Подготовка занятия", 0, "Открыть подготовку и запись занятия"),
        NavigationEntry("РАБОТА", "Транскрипт", 1, "Открыть проверку транскрипта"),
        NavigationEntry("РАБОТА", "Публикация", 2, "Открыть публикацию транскрипта"),
        NavigationEntry("РАБОТА", "Фоновая обработка", 4, "Открыть очередь фоновой обработки"),
        NavigationEntry("УЧЕНИКИ", "Ученики", 5, "Открыть карточки учеников"),
        NavigationEntry("УЧЕНИКИ", "Расписание", 6, "Открыть расписание"),
        NavigationEntry("УЧЕНИКИ", "Материалы", 7, "Открыть архив материалов"),
        NavigationEntry("ИНСТРУМЕНТЫ", "PDF и LaTeX", 3, "Открыть инструменты PDF и LaTeX"),
    )


    SIDEBAR_STYLESHEET = """
    QFrame#informationArchitectureShell {
        background: transparent;
        border: 0;
    }

    QFrame#sideNavigation {
        min-width: 214px;
        max-width: 214px;
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 16px;
    }

    QLabel#sideNavigationTitle {
        color: #111827;
        font-size: 16px;
        font-weight: 700;
    }

    QLabel#sideNavigationGroup {
        color: #7A8798;
        font-size: 10px;
        font-weight: 750;
        letter-spacing: 1px;
        padding: 10px 8px 3px 8px;
    }

    QPushButton#sideNavigationButton {
        min-height: 38px;
        padding: 0 11px;
        text-align: left;
        color: #526174;
        background: transparent;
        border: 1px solid transparent;
        border-radius: 9px;
        font-weight: 600;
    }

    QPushButton#sideNavigationButton:hover {
        color: #344054;
        background: #F1F4F8;
        border-color: #E1E6ED;
    }

    QPushButton#sideNavigationButton[active="true"] {
        color: #275AA6;
        background: #EAF2FF;
        border-color: #CFE0FA;
        font-weight: 700;
    }

    QPushButton#sideNavigationButton:focus {
        border: 2px solid #4D7FD6;
    }

    QPushButton#headerMoreButton {
        min-width: 40px;
        max-width: 40px;
        padding: 0;
        font-size: 18px;
        font-weight: 700;
    }
    """


    class SidebarNavigation(QFrame):
        quick_requested = Signal()

        def __init__(self, tabs: QTabWidget, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.tabs = tabs
            self.setObjectName("informationArchitectureShell")
            self.buttons: dict[int, QPushButton] = {}
            self.quick_button: QPushButton | None = None

            root = QHBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(14)

            sidebar = QFrame()
            sidebar.setObjectName("sideNavigation")
            sidebar_layout = QVBoxLayout(sidebar)
            sidebar_layout.setContentsMargins(12, 14, 12, 14)
            sidebar_layout.setSpacing(3)

            title = QLabel("Рабочее пространство")
            title.setObjectName("sideNavigationTitle")
            sidebar_layout.addWidget(title)

            current_group = ""
            for entry in NAVIGATION_ENTRIES:
                if entry.group != current_group:
                    current_group = entry.group
                    group_label = QLabel(entry.group)
                    group_label.setObjectName("sideNavigationGroup")
                    sidebar_layout.addWidget(group_label)
                button = QPushButton(entry.label)
                button.setObjectName("sideNavigationButton")
                button.setAccessibleName(entry.accessible_name)
                button.setProperty("active", False)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                if entry.page_index is None:
                    self.quick_button = button
                    button.clicked.connect(self.quick_requested.emit)
                else:
                    self.buttons[entry.page_index] = button
                    button.clicked.connect(
                        lambda _checked=False, index=entry.page_index: self.tabs.setCurrentIndex(index)
                    )
                sidebar_layout.addWidget(button)

            sidebar_layout.addStretch(1)
            root.addWidget(sidebar)
            root.addWidget(tabs, 1)

            self.tabs.tabBar().setVisible(False)
            self.tabs.currentChanged.connect(self._sync_active)
            self._sync_active(self.tabs.currentIndex())
            _install_stylesheet()

        def _sync_active(self, current_index: int) -> None:
            for index, button in self.buttons.items():
                button.setProperty("active", index == current_index)
                refresh_style(button)

        def button_for_page(self, page_index: int) -> QPushButton | None:
            return self.buttons.get(page_index)


    def install_information_architecture(window) -> SidebarNavigation:
        tabs = window.tabs
        stack = window.content_stack
        was_detailed = stack.currentWidget() is tabs
        navigation = SidebarNavigation(tabs)
        navigation.quick_requested.connect(lambda: window._set_mode("quick"))
        stack.removeWidget(tabs)
        stack.insertWidget(1, navigation)
        if was_detailed:
            stack.setCurrentWidget(navigation)
        return navigation


    def _install_stylesheet() -> None:
        application = QApplication.instance()
        if not isinstance(application, QApplication):
            return
        if application.property("ux1InformationArchitectureStyle"):
            return
        application.setStyleSheet(application.styleSheet() + SIDEBAR_STYLESHEET)
        application.setProperty("ux1InformationArchitectureStyle", True)
    '''
).lstrip()


TRANSCRIPT_PUBLICATION_APP = dedent(
    '''
    from __future__ import annotations

    import logging
    from pathlib import Path

    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import (
        QComboBox,
        QFormLayout,
        QFrame,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QMenu,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    from ..audio_files import finalize_readable_audio
    from ..domain import JobStatus, Lesson
    from ..publisher import publication_payload_files
    from ..recording import DualRecorder
    from . import app as base_app
    from .concurrent_app import MainWindow as ConcurrentMainWindow
    from .information_architecture import install_information_architecture
    from .library_transcription import install_library_transcription_control
    from .theme import set_button_kind

    _AUDIO_FORMAT_OPTIONS = (
        ("M4A · AAC 96 кбит/с · рекомендуется", "m4a"),
        ("MP3 · 128 кбит/с", "mp3"),
        ("WAV · PCM 16 бит", "wav"),
    )
    _TRANSCRIPTION_ENTRY_STATUSES = {
        JobStatus.DRAFT,
        JobStatus.RECORDED,
        JobStatus.REVIEW_REQUIRED,
        JobStatus.READY,
        JobStatus.FAILED,
    }
    _TRANSCRIPTION_BLOCKED_STATUSES = {
        JobStatus.RECORDING,
        JobStatus.TRANSCRIBING,
        JobStatus.COMPILING_PDF,
        JobStatus.GENERATING,
    }


    class MainWindow(ConcurrentMainWindow):
        """Production window with the UX-1 navigation and transcript-only publication."""

        def __init__(self, config_path):
            super().__init__(config_path)
            base_app.DualRecorder = self._create_configured_recorder
            install_library_transcription_control(self.student_content_page)
            self._install_audio_format_selector()

        def _build(self) -> None:
            super()._build()
            quick_mode = self.content_stack.currentWidget() is self.quick_page
            self._install_header_menu()
            self.navigation_shell = install_information_architecture(self)
            self._set_mode("quick" if quick_mode else "detailed")

        def _install_header_menu(self) -> None:
            self.header_more_button = QPushButton("⋯")
            self.header_more_button.setObjectName("headerMoreButton")
            self.header_more_button.setAccessibleName("Дополнительные действия приложения")
            self.header_more_button.setToolTip("Диагностика, журнал и настройки")
            menu = QMenu(self.header_more_button)
            menu.addAction("Собрать диагностический пакет").triggered.connect(
                lambda _checked=False: self._create_support_bundle()
            )
            menu.addAction("Открыть журнал приложения").triggered.connect(
                lambda _checked=False: self._open_logs()
            )
            menu.addSeparator()
            menu.addAction("Настройки LLM-фильтрации").triggered.connect(
                lambda _checked=False: self._show_normalization_settings()
            )
            self.header_more_button.setMenu(menu)
            self.header_layout.addWidget(self.header_more_button)

        def _set_mode(self, mode: str) -> None:
            super()._set_mode(mode)
            if not hasattr(self, "header_more_button"):
                return
            quick = mode == "quick"
            self.header_eyebrow.setVisible(False)
            self.header_subtitle.setVisible(False)
            self.support_button.setVisible(False)
            self.logs_button.setVisible(False)
            self.quick_mode_button.setVisible(False)
            self.app_status.setVisible(not quick)
            self.detailed_mode_button.setVisible(quick)
            self.detailed_mode_button.setText("Рабочее пространство")
            self.detailed_mode_button.setFixedWidth(190)
            self.detailed_mode_button.setToolTip(
                "Открыть транскрипты, публикацию, учеников, расписание и материалы"
            )
            self.header_more_button.setVisible(True)

        def _publish_tab(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(2, 4, 2, 4)
            layout.setSpacing(12)
            layout.addWidget(
                self._page_heading(
                    "Опубликуйте транскрипт",
                    "После подтверждения в main будет записан один файл transcript.txt. "
                    "Аудио и служебные материалы останутся локально.",
                )
            )
            layout.addStretch(1)
            card_row = QHBoxLayout()
            card_row.addStretch(1)
            card = QGroupBox("Готовность транскрипта")
            card.setMaximumWidth(720)
            card.setMinimumWidth(560)
            card_layout = QVBoxLayout(card)
            card_layout.setSpacing(14)
            intro = QLabel("Публикация станет доступна после подтверждения транскрипта")
            intro.setObjectName("muted")
            intro.setWordWrap(True)
            card_layout.addWidget(intro)
            summary_panel = QFrame()
            summary_panel.setObjectName("infoPanel")
            summary_panel_layout = QVBoxLayout(summary_panel)
            summary_panel_layout.setContentsMargins(16, 14, 16, 14)
            self.publish_summary = QLabel("Сначала создайте и подтвердите транскрипт.")
            self.publish_summary.setWordWrap(True)
            summary_panel_layout.addWidget(self.publish_summary)
            card_layout.addWidget(summary_panel)
            actions = QHBoxLayout()
            actions.addStretch()
            self.open_pr_button = set_button_kind(QPushButton("Открыть pull request"), "ghost")
            self.open_pr_button.setVisible(False)
            self.open_pr_button.setEnabled(False)
            self.open_pr_button.clicked.connect(self._open_current_pr)
            actions.addWidget(self.open_pr_button)
            self.publish_button = set_button_kind(
                QPushButton("Опубликовать transcript.txt в main"),
                "primary",
            )
            self.publish_button.setToolTip(
                "Передать в приватный репозиторий подтверждённый transcript.txt"
            )
            self.publish_button.setEnabled(False)
            self.publish_button.clicked.connect(self.publish)
            actions.addWidget(self.publish_button)
            card_layout.addLayout(actions)
            card_row.addWidget(card)
            card_row.addStretch(1)
            layout.addLayout(card_row)
            layout.addStretch(2)
            return page

        def _create_configured_recorder(self, *args, **kwargs) -> DualRecorder:
            kwargs["output_format"] = self.config.recording.output_format
            return DualRecorder(*args, **kwargs)

        def _install_audio_format_selector(self) -> None:
            self.audio_output_format = QComboBox()
            self.audio_output_format.setObjectName("audioOutputFormat")
            self.audio_output_format.setToolTip(
                "Чанки и внутренний мастер сохраняются в WAV. "
                "Итоговый файл кодируется после завершения записи."
            )
            for label, value in _AUDIO_FORMAT_OPTIONS:
                self.audio_output_format.addItem(label, value)
            selected_index = self.audio_output_format.findData(
                self.config.recording.output_format
            )
            if selected_index >= 0:
                self.audio_output_format.setCurrentIndex(selected_index)
            form = self.student.parentWidget().layout()
            if not isinstance(form, QFormLayout):
                raise RuntimeError("Форма параметров занятия недоступна")
            form.addRow("Итоговый формат аудио", self.audio_output_format)
            self.audio_output_format.currentIndexChanged.connect(
                self._audio_output_format_changed
            )

        def _audio_output_format_changed(self, _index: int) -> None:
            selected = str(self.audio_output_format.currentData())
            self.config.recording.output_format = selected
            self.config.save(self.config_path)
            self._set_status(f"Формат следующих записей: {selected.upper()}")

        def start_recording(self) -> None:
            self.audio_output_format.setEnabled(False)
            try:
                super().start_recording()
            finally:
                if not (self.recorder and self.recorder.active):
                    self.audio_output_format.setEnabled(True)

        def _recording_ready_impl(self, result, recorded_lesson, source_recorder, reason=None) -> None:
            readable = finalize_readable_audio(
                result,
                recorded_lesson.student.full_name,
                recorded_lesson.lesson_date,
            )
            super()._recording_ready_impl(readable, recorded_lesson, source_recorder, reason)

        def _recording_ready(self, *args, **kwargs) -> None:
            try:
                super()._recording_ready(*args, **kwargs)
            finally:
                self.audio_output_format.setEnabled(True)

        def _recording_stop_failed(self, *args, **kwargs) -> None:
            try:
                super()._recording_stop_failed(*args, **kwargs)
            finally:
                self.audio_output_format.setEnabled(True)

        def _recovery_ready(self, result) -> None:
            lesson_id = result.session_file.parent.parent.name
            lesson = self.pipeline.store.get(lesson_id)
            if lesson is not None:
                result = finalize_readable_audio(
                    result,
                    lesson.student.full_name,
                    lesson.lesson_date,
                )
            super()._recovery_ready(result)

        def _queue_imported_audio(self, lesson: Lesson, audio: Path) -> None:
            if lesson.status in _TRANSCRIPTION_BLOCKED_STATUSES:
                self._set_status(
                    f"{lesson.student.full_name}: занятие занято другой операцией",
                    "warning",
                )
                return
            if lesson.status not in _TRANSCRIPTION_ENTRY_STATUSES:
                lesson.transition(JobStatus.RECORDED, force=True)
                self.pipeline.save_state(
                    lesson,
                    "status",
                    "error",
                    force_status=True,
                )
            super()._queue_imported_audio(lesson, audio)
            self.student_content_page.refresh_if_loaded()

        def _background_transcription_ready(self, job_id: str, lesson: Lesson) -> None:
            super()._background_transcription_ready(job_id, lesson)
            self.student_content_page.refresh_if_loaded()

        def _background_transcription_failed(self, job_id: str, details: str) -> None:
            super()._background_transcription_failed(job_id, details)
            self.student_content_page.refresh_if_loaded()

        def approve_transcript(self) -> None:
            super().approve_transcript()
            if not self.lesson or self.lesson.status != JobStatus.READY:
                return
            payload = "\n".join(f"• {path}" for path in publication_payload_files(self.lesson))
            self.publish_summary.setText(
                f"{self.lesson.student.full_name}\n"
                f"{self.lesson.lesson_date:%d.%m.%Y}\n"
                f"{self.lesson.topic}\n\n"
                "Будет опубликован ровно один файл:\n"
                f"{payload}\n\n"
                "Ветка: main\n"
                "Аудио, JSON, TEX, PDF, изображения и журналы останутся на компьютере."
            )

        def publish(self) -> None:
            if not self.lesson or self.lesson.status != JobStatus.READY:
                QMessageBox.warning(self, "Публикация", "Сначала подтвердите транскрипт")
                return
            self.publish_button.setEnabled(False)
            self._set_status("Публикую transcript.txt в main…", "working")
            logging.info("Transcript-only публикация начата: lesson=%s", self.lesson.lesson_id)
            worker = base_app.Worker(self.pipeline.publish, self.lesson)
            worker.succeeded.connect(self._publication_ready)
            worker.failed.connect(lambda details: self._operation_failed("publish", details))
            worker.finished.connect(lambda: self._worker_finished(worker))
            self.workers.append(worker)
            worker.start()

        def _publication_ready(self, result) -> None:
            details = (
                "Опубликован один файл transcript.txt\n"
                f"Ветка: {result.branch}\n"
                f"Commit: {result.commit[:12]}\n"
                f"Путь: {result.repository_path}\n\n"
                "Остальные файлы занятия сохранены локально."
            )
            if result.warnings:
                details += "\n\n" + "\n".join(result.warnings)
            QMessageBox.information(self, "Публикация завершена", details)
            self.latex_monitor_status.setText(
                "Удалённая публикация содержит transcript.txt; производные материалы остаются локально"
            )
            self._set_status("transcript.txt опубликован в main")
            self.publish_summary.setText(details)
            logging.info(
                "Transcript-only публикация завершена: branch=%s commit=%s path=%s",
                result.branch,
                result.commit,
                result.repository_path,
            )

        def closeEvent(self, event: QCloseEvent) -> None:
            super().closeEvent(event)
            if event.isAccepted():
                base_app.DualRecorder = DualRecorder


    def main() -> None:
        base_app.MainWindow = MainWindow
        base_app.main()


    if __name__ == "__main__":
        main()
    '''
).lstrip()


TEST_INFORMATION_ARCHITECTURE = dedent(
    '''
    from __future__ import annotations

    import inspect

    from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

    from tutor_assistant.ui.information_architecture import SidebarNavigation
    from tutor_assistant.ui.transcript_publication_app import MainWindow

    _APPLICATION: QApplication | None = None


    def _application() -> QApplication:
        global _APPLICATION
        existing = QApplication.instance()
        if isinstance(existing, QApplication):
            _APPLICATION = existing
        elif _APPLICATION is None:
            _APPLICATION = QApplication([])
        return _APPLICATION


    def test_sidebar_groups_pages_and_keeps_tab_indices() -> None:
        _application()
        tabs = QTabWidget()
        for index in range(8):
            tabs.addTab(QWidget(), f"Page {index}")
        navigation = SidebarNavigation(tabs)

        assert tabs.tabBar().isHidden()
        assert navigation.button_for_page(0).text() == "Подготовка занятия"
        assert navigation.button_for_page(5).text() == "Ученики"
        assert navigation.button_for_page(3).text() == "PDF и LaTeX"

        navigation.button_for_page(6).click()

        assert tabs.currentIndex() == 6
        assert navigation.button_for_page(6).property("active") is True
        assert navigation.button_for_page(0).property("active") is False


    def test_production_window_builds_publication_policy_directly() -> None:
        source = inspect.getsource(MainWindow)

        assert "install_information_architecture" in source
        assert "Опубликуйте транскрипт" in source
        assert "Опубликовать transcript.txt в main" in source
        assert "findChildren(QLabel)" not in source
        assert "label.text() ==" not in source
    '''
).lstrip()


TEST_LOCALIZATION = dedent(
    '''
    from __future__ import annotations

    from PySide6.QtWidgets import QApplication, QComboBox

    from tutor_assistant.ui.localization import (
        contact_channel_label,
        parse_subject_list,
        select_subject,
        set_subject_combo,
        subject_label,
        subject_list_text,
        subject_value,
    )

    _APPLICATION: QApplication | None = None


    def _application() -> QApplication:
        global _APPLICATION
        existing = QApplication.instance()
        if isinstance(existing, QApplication):
            _APPLICATION = existing
        elif _APPLICATION is None:
            _APPLICATION = QApplication([])
        return _APPLICATION


    def test_subject_combo_shows_russian_and_keeps_canonical_values() -> None:
        _application()
        combo = QComboBox()
        set_subject_combo(combo, selected="physics")

        assert combo.currentText() == "Физика"
        assert combo.currentData() == "physics"
        assert combo.itemText(combo.findData("mathematics")) == "Математика"

        assert select_subject(combo, "Химия") is True
        assert combo.currentData() == "chemistry"


    def test_subject_and_contact_enums_round_trip() -> None:
        assert subject_label("mathematics") == "Математика"
        assert subject_value("Математика") == "mathematics"
        assert subject_list_text(["mathematics", "physics"]) == "Математика, Физика"
        assert parse_subject_list("Математика, chemistry") == ["mathematics", "chemistry"]
        assert contact_channel_label("social") == "Социальная сеть"
    '''
).lstrip()


write("src/tutor_assistant/ui/localization.py", LOCALIZATION)
write("src/tutor_assistant/ui/information_architecture.py", INFORMATION_ARCHITECTURE)
write("src/tutor_assistant/ui/transcript_publication_app.py", TRANSCRIPT_PUBLICATION_APP)
write("tests/test_information_architecture_gui.py", TEST_INFORMATION_ARCHITECTURE)
write("tests/test_ui_localization.py", TEST_LOCALIZATION)

replace_once(
    "src/tutor_assistant/ui/app.py",
    "from .crm import SchedulePage, StudentsPage\nfrom .normalization import NormalizationReviewDialog\n",
    "from .crm import SchedulePage, StudentsPage\nfrom .localization import select_subject, set_subject_combo, subject_value\nfrom .normalization import NormalizationReviewDialog\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "        subject_index = self.quick_subject.findText(subject)\n        if subject_index >= 0:\n            self.quick_subject.setCurrentIndex(subject_index)\n",
    "        select_subject(self.quick_subject, subject)\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "        self.quick_subject = QComboBox()\n        self.quick_subject.setToolTip(\"Предмет определяет папку и шаблоны материалов\")\n        self.quick_subject.addItems([\"mathematics\", \"physics\", \"chemistry\"])\n        subject = self.config.quick_start.last_subject or profile.subject\n        subject_index = self.quick_subject.findText(subject)\n        if subject_index >= 0:\n            self.quick_subject.setCurrentIndex(subject_index)\n",
    "        self.quick_subject = QComboBox()\n        self.quick_subject.setToolTip(\"Предмет определяет папку и шаблоны материалов\")\n        set_subject_combo(\n            self.quick_subject,\n            selected=self.config.quick_start.last_subject or profile.subject,\n        )\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "        subject_index = self.subject.findText(self.quick_subject.currentText())\n        if subject_index >= 0:\n            self.subject.setCurrentIndex(subject_index)\n",
    "        selected_subject = subject_value(\n            self.quick_subject.currentData() or self.quick_subject.currentText()\n        )\n        select_subject(self.subject, selected_subject)\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "        self.config.quick_start.last_subject = self.quick_subject.currentText()\n",
    "        self.config.quick_start.last_subject = selected_subject\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "        index = self.quick_subject.findText(profile.subject)\n        if index >= 0:\n            self.quick_subject.setCurrentIndex(index)\n",
    "        select_subject(self.quick_subject, profile.subject)\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "        self.subject = QComboBox()\n        self.subject.addItems([\"mathematics\", \"physics\", \"chemistry\"])\n",
    "        self.subject = QComboBox()\n        set_subject_combo(self.subject)\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "            subject=self.subject.currentText(),\n",
    "            subject=subject_value(self.subject.currentData() or self.subject.currentText()),\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "        index = self.subject.findText(lesson.subject)\n",
    "        index = self.subject.findData(lesson.subject)\n",
)

replace_once(
    "src/tutor_assistant/ui/crm.py",
    "from .theme import set_button_kind\n\nSUBJECTS = [\"mathematics\", \"physics\", \"chemistry\"]\n",
    "from .localization import (\n    contact_channel_label,\n    parse_subject_list,\n    select_subject,\n    set_subject_combo,\n    subject_label,\n    subject_list_text,\n    subject_value,\n)\nfrom .theme import set_button_kind\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "        self.subjects.setPlaceholderText(\"mathematics, physics\")\n",
    "        self.subjects.setPlaceholderText(\"Математика, Физика\")\n",
)
replace_all(
    "src/tutor_assistant/ui/crm.py",
    '", ".join(profile.subjects)',
    "subject_list_text(profile.subjects)",
    minimum=2,
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "                guardian.preferred_contact,\n",
    "                contact_channel_label(guardian.preferred_contact),\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '                subjects=[item.strip() for item in self.subjects.text().split(",") if item.strip()],\n',
    "                subjects=parse_subject_list(self.subjects.text()),\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "        self.subject = QComboBox()\n        self.subject.setEditable(True)\n        self.subject.addItems(SUBJECTS)\n",
    "        self.subject = QComboBox()\n        self.subject.setEditable(True)\n        set_subject_combo(self.subject)\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "            self.subject.setCurrentText(lesson.subject)\n",
    "            select_subject(self.subject, lesson.subject)\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "                self.subject.setCurrentText(profile.subjects[0])\n",
    "                select_subject(self.subject, profile.subjects[0])\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "            subject=self.subject.currentText().strip(),\n",
    "            subject=subject_value(self.subject.currentText()),\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    '                f"{lesson.starts_at:%H:%M}  {lesson.student_name}\\n{lesson.subject}"\n',
    '                f"{lesson.starts_at:%H:%M}  {lesson.student_name}\\n{subject_label(lesson.subject)}"\n',
)

replace_once(
    "src/tutor_assistant/ui/student_content.py",
    "from .playback import PlaybackPanel, QtPlaybackBackend\nfrom .theme import set_button_kind\n",
    "from .localization import subject_label\nfrom .playback import PlaybackPanel, QtPlaybackBackend\nfrom .theme import set_button_kind\n",
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    "            self.subject_filter.addItem(subject, subject)\n",
    "            self.subject_filter.addItem(subject_label(subject), subject)\n",
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    "                lesson.student.full_name,\n                lesson.subject,\n                lesson.topic,\n",
    "                lesson.student.full_name,\n                subject_label(lesson.subject),\n                lesson.topic,\n",
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    '        self.metadata["subject"].setText(lesson.subject)\n',
    '        self.metadata["subject"].setText(subject_label(lesson.subject))\n',
)

replace_once(
    "pyproject.toml",
    'version = "0.18.0"',
    'version = "0.19.0"',
)
replace_once(
    "src/tutor_assistant/__init__.py",
    '__version__ = "0.18.0"',
    '__version__ = "0.19.0"',
)
replace_once(
    "README.md",
    "Текущая версия: **0.18.0**.",
    "Текущая версия: **0.19.0**.",
)
replace_once(
    "README.md",
    "## Требования\n",
    dedent(
        '''
        ## Что добавлено в 0.19.0

        - боковая навигация вместо восьми горизонтальных вкладок;
        - разделение на группы «Работа», «Ученики» и «Инструменты»;
        - компактная верхняя панель с единым меню диагностики и настроек;
        - русские названия предметов при сохранении канонических значений в данных;
        - локализованные технические значения каналов связи;
        - самостоятельный transcript-only экран публикации без поиска и замены текстовых виджетов;
        - GUI-контракты информационной архитектуры и локализации.

        ## Требования
        '''
    ),
)

print("UX-1 information architecture patch applied")
