from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QPushButton, QVBoxLayout

from ..content_browser import is_audio_path
from ..domain import JobStatus
from .theme import set_button_kind

_BLOCKED_STATUSES = {
    JobStatus.RECORDING,
    JobStatus.TRANSCRIBING,
    JobStatus.COMPILING_PDF,
    JobStatus.GENERATING,
}


def _selected_audio(page) -> Path | None:
    items = page.files_table.selectedItems()
    value = items[0].data(Qt.UserRole) if items else None
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_file() and is_audio_path(path) else None


def install_library_transcription_control(page) -> QPushButton:
    """Add retranscription of any indexed audio file to StudentContentPage."""

    button = set_button_kind(QPushButton("Транскрибировать аудио"), "primary")
    button.setObjectName("transcribeSelectedAudioButton")
    button.setAccessibleName("Транскрибировать выбранный аудиофайл")
    button.setToolTip("Добавить выбранную аудиодорожку занятия в фоновую очередь Whisper")
    button.setEnabled(False)

    layout = page.files_table.parentWidget().layout()
    if not isinstance(layout, QVBoxLayout):
        raise RuntimeError("Компоновка файлов архива недоступна")
    index = layout.indexOf(page.files_table)
    layout.insertWidget(index, button, 0, Qt.AlignmentFlag.AlignRight)
    page.transcribe_audio_button = button

    def sync() -> None:
        content = page._current_content
        audio = _selected_audio(page)
        allowed = bool(
            content
            and audio
            and not page._transcript_editing
            and content.lesson.status not in _BLOCKED_STATUSES
        )
        button.setEnabled(allowed)

    def request() -> None:
        content = page._current_content
        audio = _selected_audio(page)
        if content is None or audio is None:
            return
        lesson = content.lesson
        if lesson.status in _BLOCKED_STATUSES:
            QMessageBox.warning(
                page,
                "Транскрибация занята",
                "Для выбранного занятия уже выполняется операция, несовместимая с транскрибацией.",
            )
            sync()
            return
        if content.transcript is not None:
            answer = QMessageBox.question(
                page,
                "Повторная транскрибация",
                "Создать новый транскрипт из выбранного аудио? "
                "Подтверждённая версия сохранится в истории до принятия нового результата.",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return
        button.setEnabled(False)
        page.audio_queue_requested.emit(lesson.model_copy(deep=True), audio)
        page.status_changed.emit(
            f"{lesson.student.full_name}: аудио добавлено в очередь транскрибации",
            "working",
        )

    page.files_table.itemSelectionChanged.connect(sync)
    content_changed = getattr(page, "content_changed", None)
    if content_changed is not None:
        content_changed.connect(sync)
    elif hasattr(page, "details_dialog"):
        page.details_dialog.finished.connect(lambda _result: sync())
    button.clicked.connect(request)
    return button
