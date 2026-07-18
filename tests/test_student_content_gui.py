from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tutor_assistant.content import StudentContentService  # noqa: E402
from tutor_assistant.domain import Lesson, Student  # noqa: E402
from tutor_assistant.playback import PlaybackController  # noqa: E402
from tutor_assistant.ui.student_content import StudentContentPage  # noqa: E402


class FakePlaybackBackend(QObject):
    position_changed = Signal(int)
    duration_changed = Signal(int)
    playing_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.position = 0
        self.playing = False

    def load(self, _path: Path) -> None:
        self.position = 0

    def play(self) -> None:
        self.playing = True
        self.playing_changed.emit(True)

    def pause(self) -> None:
        self.playing = False
        self.playing_changed.emit(False)

    def stop(self) -> None:
        self.pause()

    def set_position(self, position_ms: int) -> None:
        self.position = position_ms
        self.position_changed.emit(position_ms)

    def position_ms(self) -> int:
        return self.position

    def set_rate(self, _rate: float) -> None:
        return

    def is_playing(self) -> bool:
        return self.playing


class FakeScheduler:
    def schedule(self, _delay_ms: int, _callback) -> None:
        return

    def cancel(self) -> None:
        return


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_page(tmp_path: Path) -> tuple[StudentContentPage, StudentContentService]:
    service = StudentContentService(tmp_path / "data")
    student = Student(id="student", full_name="Ученик")
    service.create_lesson(
        Lesson(
            lesson_id="gui-lesson",
            student=student,
            subject="mathematics",
            lesson_date=date(2026, 7, 18),
            topic="GUI hardening",
        )
    )
    backend = FakePlaybackBackend()
    controller = PlaybackController(backend, FakeScheduler(), lambda: True)

    def run_background(callable_, succeeded, failed) -> None:
        try:
            succeeded(callable_())
        except Exception as exc:  # pragma: no cover - assertion path reports through the UI
            failed(str(exc))

    page = StudentContentPage(service, [student], run_background, controller, backend)
    page.ensure_loaded()
    QApplication.processEvents()
    return page, service


def test_archive_accessibility_filters_delete_and_restore(
    tmp_path: Path,
    application: QApplication,
    monkeypatch,
) -> None:
    page, service = make_page(tmp_path)

    assert page.accessibleName() == "Архив материалов учеников"
    assert page.search.accessibleName() == "Полнотекстовый поиск по материалам"
    assert page.table.accessibleName() == "Список занятий"
    assert page.playback_panel.play_pause.accessibleName() == ("Воспроизвести или приостановить аудио")
    assert page.table.rowCount() == 1

    page.search_shortcut.activated.emit()
    assert page.search.hasFocus()
    page.search.setText("GUI hardening")
    page.search_timer.stop()
    page._filters_changed_now()
    assert page.table.rowCount() == 1

    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)
    page.delete_shortcut.activated.emit()
    assert service.list_lessons().total == 0

    page.open_trash()
    dialog = page.trash_dialog
    assert dialog is not None
    assert dialog.table.accessibleName() == "Удалённые занятия"
    dialog.table.selectRow(0)
    dialog.restore_shortcut.activated.emit()
    assert service.list_lessons().total == 1
    dialog.close()
    page.close()


def test_storage_diagnostics_dialog_is_keyboard_and_screen_reader_ready(
    tmp_path: Path,
    application: QApplication,
) -> None:
    page, _service = make_page(tmp_path)

    page.open_content_health()
    dialog = page.health_dialog

    assert dialog is not None
    assert dialog.accessibleName() == "Диагностика локального архива материалов"
    assert dialog.table.accessibleName() == "Обнаруженные проблемы хранилища"
    assert "SQLite: ok" in dialog.summary.text()
    assert "свободно:" in dialog.storage.text()
    dialog.rescan_shortcut.activated.emit()
    assert dialog.rescan_button.isEnabled()
    dialog.close()
    page.close()
