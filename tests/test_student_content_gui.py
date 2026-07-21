from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tutor_assistant.content import StudentContentService  # noqa: E402
from tutor_assistant.domain import JobStatus, Lesson, Student  # noqa: E402
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
    page.show()
    application.processEvents()

    assert page.accessibleName() == "Архив материалов учеников"
    assert page.search.accessibleName() == "Полнотекстовый поиск по материалам"
    assert page.table.accessibleName() == "Список занятий"
    assert page.playback_panel.play_pause.accessibleName() == ("Воспроизвести или приостановить аудио")
    assert page.table.rowCount() == 1
    assert not page.details_dialog.isVisible()

    page.table.selectRow(0)
    application.processEvents()
    assert page.details_dialog.isVisible()
    assert page.details_dialog.accessibleName() == "Содержимое занятия"
    assert page.metadata["topic"].text() == "GUI hardening"
    page.close_details()
    application.processEvents()

    page.search_shortcut.activated.emit()
    application.processEvents()
    assert page.search.hasFocus()
    page.search.setText("GUI hardening")
    page.search_timer.stop()
    page._filters_changed_now()
    assert page.table.rowCount() == 1

    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)
    page.table.selectRow(0)
    application.processEvents()
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
    page, service = make_page(tmp_path)

    page.open_content_health()
    dialog = page.health_dialog

    assert dialog is not None
    assert dialog.accessibleName() == "Диагностика локального архива материалов"
    assert dialog.table.accessibleName() == "Обнаруженные проблемы хранилища"
    assert "SQLite: ok" in dialog.summary.text()
    assert "свободно:" in dialog.storage.text()
    dialog.rescan_shortcut.activated.emit()
    assert dialog.rescan_button.isEnabled()
    unregistered = service.workspace / "lessons" / "gui-lesson" / "result.pdf"
    unregistered.write_bytes(b"%PDF-gui-repair")
    dialog.rescan_shortcut.activated.emit()
    assert dialog.repair_button.isEnabled()
    dialog.repair_button.click()
    assert any(
        asset.relative_path.endswith("result.pdf") for asset in service.get_lesson("gui-lesson").assets
    )
    dialog.close()
    page.close()


def test_active_lesson_delete_is_a_warning_without_background_failure(
    tmp_path: Path,
    application: QApplication,
    monkeypatch,
) -> None:
    page, service = make_page(tmp_path)
    content = service.get_lesson("gui-lesson")
    lesson = content.lesson
    lesson.transition(JobStatus.RECORDING, force=True)
    service.repository.upsert_lesson(lesson)
    page.refresh()
    page.table.selectRow(0)
    application.processEvents()

    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message, *_args, **_kwargs: warnings.append((title, message)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("confirmation must not open for active lesson")
        ),
    )

    page.delete_selected_lesson()

    assert warnings == [
        (
            "Удаление недоступно",
            "Нельзя удалить занятие во время записи или транскрибации",
        )
    ]
    assert service.list_lessons().total == 1
    page.close()
