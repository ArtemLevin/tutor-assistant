from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tutor_assistant.domain import JobStatus, Lesson, Student  # noqa: E402
from tutor_assistant.ui.library_transcription import (  # noqa: E402
    install_library_transcription_control,
)


class FakePage(QWidget):
    audio_queue_requested = Signal(object, object)
    status_changed = Signal(str, str)

    def __init__(self, lesson: Lesson) -> None:
        super().__init__()
        self._current_content = SimpleNamespace(lesson=lesson, transcript=None)
        self._transcript_editing = False
        layout = QVBoxLayout(self)
        self.files_table = QTableWidget(0, 1)
        layout.addWidget(self.files_table)
        self.details_dialog = QDialog(self)


def _lesson(status: JobStatus = JobStatus.PUBLISHED) -> Lesson:
    return Lesson(
        lesson_id="library-transcription",
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 31),
        topic="Архивное аудио",
        status=status,
    )


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_selected_audio_is_emitted_for_background_queue(
    tmp_path: Path,
    application: QApplication,
) -> None:
    audio = tmp_path / "system.wav"
    audio.write_bytes(b"audio")
    page = FakePage(_lesson())
    button = install_library_transcription_control(page)
    page.files_table.setRowCount(1)
    item = QTableWidgetItem("system.wav")
    item.setData(Qt.UserRole, str(audio))
    page.files_table.setItem(0, 0, item)
    page.files_table.selectRow(0)
    application.processEvents()
    queued: list[tuple[Lesson, Path]] = []
    page.audio_queue_requested.connect(
        lambda lesson, path: queued.append((lesson, path))
    )

    assert button.isEnabled()
    button.click()

    assert len(queued) == 1
    assert queued[0][0].lesson_id == "library-transcription"
    assert queued[0][1] == audio


def test_non_audio_file_does_not_enable_transcription(
    tmp_path: Path,
    application: QApplication,
) -> None:
    document = tmp_path / "lesson.json"
    document.write_text("{}", encoding="utf-8")
    page = FakePage(_lesson())
    button = install_library_transcription_control(page)
    page.files_table.setRowCount(1)
    item = QTableWidgetItem("lesson.json")
    item.setData(Qt.UserRole, str(document))
    page.files_table.setItem(0, 0, item)
    page.files_table.selectRow(0)
    application.processEvents()

    assert not button.isEnabled()


def test_active_transcription_disables_archive_action(
    tmp_path: Path,
    application: QApplication,
) -> None:
    audio = tmp_path / "lesson.m4a"
    audio.write_bytes(b"audio")
    page = FakePage(_lesson(JobStatus.TRANSCRIBING))
    button = install_library_transcription_control(page)
    page.files_table.setRowCount(1)
    item = QTableWidgetItem("lesson.m4a")
    item.setData(Qt.UserRole, str(audio))
    page.files_table.setItem(0, 0, item)
    page.files_table.selectRow(0)
    application.processEvents()

    assert not button.isEnabled()
