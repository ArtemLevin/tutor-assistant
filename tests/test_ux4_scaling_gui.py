from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from tutor_assistant.content import StudentContentService
from tutor_assistant.domain import Lesson, Student
from tutor_assistant.playback import PlaybackController
from tutor_assistant.ui.accessibility import apply_accessibility_to_widget
from tutor_assistant.ui.information_architecture import SidebarNavigation
from tutor_assistant.ui.student_content import StudentContentPage


class FakePlaybackBackend(QObject):
    position_changed = Signal(int)
    duration_changed = Signal(int)
    playing_changed = Signal(bool)
    error_occurred = Signal(str)

    def load(self, _path: Path) -> None:
        self.position_changed.emit(0)

    def play(self) -> None:
        self.playing_changed.emit(True)

    def pause(self) -> None:
        self.playing_changed.emit(False)

    def stop(self) -> None:
        self.playing_changed.emit(False)

    def set_position(self, position_ms: int) -> None:
        self.position_changed.emit(position_ms)

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


def test_qt_uses_requested_windows_scale(application: QApplication) -> None:
    requested = float(os.environ.get("QT_SCALE_FACTOR", "1"))
    screen = application.primaryScreen()
    assert screen is not None
    ratio = screen.devicePixelRatio()
    assert ratio >= 1.0
    assert abs(ratio - requested) <= 0.35


def test_sidebar_and_materials_keep_usable_geometry(
    tmp_path: Path,
    application: QApplication,
) -> None:
    tabs = QTabWidget()
    for index in range(8):
        page = QWidget()
        QVBoxLayout(page)
        tabs.addTab(page, str(index))
    navigation = SidebarNavigation(tabs)
    window = QMainWindow()
    window.setCentralWidget(navigation)
    window.resize(1280, 900)
    window.show()
    application.processEvents()

    sidebar = navigation.findChild(QWidget, "sideNavigation")
    assert sidebar is not None
    assert sidebar.width() >= 214
    for button in navigation.ordered_buttons():
        assert button.height() >= 38
        assert button.width() > button.fontMetrics().horizontalAdvance(button.text()) + 20
    window.close()

    service = StudentContentService(tmp_path / "data")
    student = Student(id="student", full_name="Ученик")
    service.create_lesson(
        Lesson(
            lesson_id="ux4-scale",
            student=student,
            subject="mathematics",
            lesson_date=date(2026, 8, 1),
            topic="Windows scaling",
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
    apply_accessibility_to_widget(page)
    page.ensure_loaded()
    page.resize(1280, 900)
    page.show()
    application.processEvents()
    sizes = page.content_splitter.sizes()
    assert len(sizes) == 2
    assert min(sizes) >= 240
    assert page.import_button.height() >= 38
    assert page.table.width() > 300
    page.close()
