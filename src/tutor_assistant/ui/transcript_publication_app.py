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

from ..domain import JobStatus, Lesson
from ..publisher import publication_payload_files
from . import app as base_app
from .accessibility import install_accessibility
from .concurrent_app import MainWindow as ConcurrentMainWindow
from .information_architecture import install_information_architecture
from .library_transcription import install_library_transcription_control
from .teacher_cockpit import install_teacher_cockpit
from .theme import set_button_kind

_AUDIO_FORMAT_OPTIONS = (
    ("M4A · AAC 96 кбит/с · рекомендуется", "m4a"),
    ("MP3 · 128 кбит/с", "mp3"),
    ("WAV · PCM 16 бит", "wav"),
)
_TRANSCRIPTION_PROVIDER_OPTIONS = (
    ("faster-whisper · текущий локальный движок", "faster_whisper"),
    ("GigaAM v3 e2e RNNT · локально · русский", "gigaam"),
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
    """Production window with the UX-6 teacher cockpit and transcript publication."""

    def __init__(self, config_path):
        super().__init__(config_path)
        install_library_transcription_control(self.student_content_page)
        self._install_audio_format_selector()
        self._install_transcription_provider_selector()

    def _build(self) -> None:
        super()._build()
        default_mode = (
            "quick" if self.content_stack.currentWidget() is self.quick_page else "detailed"
        )
        self._install_header_menu()
        self.teacher_cockpit = install_teacher_cockpit(self)
        self.navigation_shell = install_information_architecture(self)
        self.teacher_cockpit.bind_navigation(self.navigation_shell)
        self.teacher_cockpit.restore_session(default_mode)
        install_accessibility(self)

    def _install_header_menu(self) -> None:
        self.header_more_button = QPushButton("⋯")
        self.header_more_button.setObjectName("headerMoreButton")
        self.header_more_button.setAccessibleName("Дополнительные действия приложения")
        self.header_more_button.setToolTip("Диагностика, журнал и настройки")
        menu = QMenu(self.header_more_button)
        menu.addAction("Открыть командную палитру").triggered.connect(
            lambda _checked=False: self._open_command_palette()
        )
        menu.addSeparator()
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

    def _open_command_palette(self) -> None:
        cockpit = getattr(self, "teacher_cockpit", None)
        if cockpit is not None:
            cockpit.open_palette()

    def _refresh_teacher_cockpit(self) -> None:
        cockpit = getattr(self, "teacher_cockpit", None)
        if cockpit is not None:
            cockpit.refresh()

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
            "Открыть обзор дня, транскрипты, учеников, расписание и материалы"
        )
        self.header_more_button.setVisible(True)
        cockpit = getattr(self, "teacher_cockpit", None)
        if cockpit is not None:
            cockpit.context_bar.setVisible(not quick)
            if cockpit.session is not None:
                cockpit.session.record_mode(mode)

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

    def _lesson_form(self) -> QFormLayout:
        form = self.student.parentWidget().layout()
        if not isinstance(form, QFormLayout):
            raise RuntimeError("Форма параметров занятия недоступна")
        return form

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
        self._lesson_form().addRow("Итоговый формат аудио", self.audio_output_format)
        self.audio_output_format.currentIndexChanged.connect(
            self._audio_output_format_changed
        )

    def _install_transcription_provider_selector(self) -> None:
        self.transcription_provider = QComboBox()
        self.transcription_provider.setObjectName("transcriptionProvider")
        self.transcription_provider.setAccessibleName("Транскрибатор")
        self.transcription_provider.setToolTip(
            "Выберите локальный движок распознавания речи. "
            "faster-whisper остаётся вариантом по умолчанию; GigaAM требует отдельной установки."
        )
        for label, value in _TRANSCRIPTION_PROVIDER_OPTIONS:
            self.transcription_provider.addItem(label, value)
        selected_index = self.transcription_provider.findData(self.config.whisper.provider)
        if selected_index >= 0:
            self.transcription_provider.setCurrentIndex(selected_index)
        self._lesson_form().addRow("Транскрибатор", self.transcription_provider)
        self.transcription_provider.currentIndexChanged.connect(
            self._transcription_provider_changed
        )

    def _audio_output_format_changed(self, _index: int) -> None:
        selected = str(self.audio_output_format.currentData())
        self.config.recording.output_format = selected
        self.config.save(self.config_path)
        self._set_status(f"Формат следующих записей: {selected.upper()}")

    def _transcription_provider_changed(self, _index: int) -> None:
        selected = str(self.transcription_provider.currentData())
        current = self.config.whisper.provider
        if selected == current:
            return
        if self.transcription_queue.unfinished_count:
            self.transcription_provider.blockSignals(True)
            try:
                restore_index = self.transcription_provider.findData(current)
                if restore_index >= 0:
                    self.transcription_provider.setCurrentIndex(restore_index)
            finally:
                self.transcription_provider.blockSignals(False)
            QMessageBox.warning(
                self,
                "Транскрибатор",
                "Дождитесь завершения текущей очереди транскрибации, "
                "затем смените движок.",
            )
            return
        self.config.whisper.provider = selected
        self.config.save(self.config_path)
        if selected == "gigaam":
            self._set_status(
                "Транскрибатор: GigaAM v3 e2e RNNT. "
                "При первом запуске модель будет загружена локально.",
                "working",
            )
        else:
            self._set_status("Транскрибатор: faster-whisper")

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
        self._refresh_teacher_cockpit()

    def _background_transcription_ready(self, job_id: str, lesson: Lesson) -> None:
        super()._background_transcription_ready(job_id, lesson)
        self.student_content_page.refresh_if_loaded()
        self._refresh_teacher_cockpit()

    def _background_transcription_failed(self, job_id: str, details: str) -> None:
        super()._background_transcription_failed(job_id, details)
        self.student_content_page.refresh_if_loaded()
        self._refresh_teacher_cockpit()

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
        self._refresh_teacher_cockpit()

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
        self._refresh_teacher_cockpit()

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
        self._refresh_teacher_cockpit()

    def closeEvent(self, event: QCloseEvent) -> None:
        cockpit = getattr(self, "teacher_cockpit", None)
        if cockpit is not None and cockpit.session is not None:
            cockpit.session.save_all()
        super().closeEvent(event)


def main() -> None:
    base_app.main(MainWindow)


if __name__ == "__main__":
    main()
