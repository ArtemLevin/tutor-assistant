from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QSplitter, QTabWidget, QWidget

from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.ui.app_routes import (
    AppRoute,
    ROUTE_DEFINITIONS,
    page_for_route,
    route_for_page,
)
from tutor_assistant.ui.command_palette import CommandPalette, PaletteCommand
from tutor_assistant.ui.information_architecture import SidebarNavigation
from tutor_assistant.ui.teacher_cockpit import (
    GlobalContextBar,
    TeacherCockpitPage,
    build_cockpit_snapshot,
)
from tutor_assistant.ui.ui_session import UISessionStore

_APPLICATION: QApplication | None = None


def _application() -> QApplication:
    global _APPLICATION
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        _APPLICATION = existing
    elif _APPLICATION is None:
        _APPLICATION = QApplication([])
    return _APPLICATION


def _lesson(status: JobStatus = JobStatus.REVIEW_REQUIRED) -> Lesson:
    return Lesson(
        student=Student(id="sofya", full_name="Софья Кальной"),
        subject="mathematics",
        lesson_date=date(2026, 8, 1),
        topic="Метод интервалов",
        status=status,
    )


def _window_stub(status: JobStatus = JobStatus.REVIEW_REQUIRED):
    return SimpleNamespace(
        lesson=_lesson(status),
        students=[Student(id="sofya", full_name="Софья Кальной")],
        workers=[],
        crm_store=None,
        config=SimpleNamespace(
            normalization=SimpleNamespace(provider="ollama")
        ),
    )


def test_route_registry_preserves_legacy_indices() -> None:
    assert page_for_route(AppRoute.LESSON) == 0
    assert page_for_route(AppRoute.MATERIALS) == 7
    assert page_for_route(AppRoute.TODAY) == 8
    assert route_for_page(3) == AppRoute.LATEX
    assert len({definition.shortcut for definition in ROUTE_DEFINITIONS}) == len(
        ROUTE_DEFINITIONS
    )


def test_sidebar_routes_badges_and_compact_mode() -> None:
    _application()
    tabs = QTabWidget()
    for index in range(9):
        tabs.addTab(QWidget(), f"Page {index}")
    navigation = SidebarNavigation(tabs)
    announced: list[str] = []
    navigation.route_changed.connect(announced.append)

    assert navigation.navigate(AppRoute.TODAY)
    assert tabs.currentIndex() == 8
    assert navigation.current_route() == AppRoute.TODAY
    assert announced[-1] == AppRoute.TODAY.value

    navigation.set_badges({AppRoute.TRANSCRIPT: 3})
    transcript_button = navigation.button_for_route(AppRoute.TRANSCRIPT)
    assert transcript_button is not None
    assert "3" in transcript_button.text()
    assert "3" in transcript_button.accessibleDescription()

    navigation.set_collapsed(True)
    assert navigation.is_collapsed()
    assert navigation.sidebar.width() == navigation.collapsed_width
    assert transcript_button.text().startswith("T")
    navigation.set_collapsed(False)
    assert navigation.sidebar.width() == navigation.expanded_width


def test_snapshot_pipeline_and_attention_follow_lesson_status() -> None:
    snapshot = build_cockpit_snapshot(
        _window_stub(JobStatus.REVIEW_REQUIRED),
        now=datetime(2026, 8, 1, 17, 0),
    )

    assert len(snapshot.pipeline) == 6
    review = next(stage for stage in snapshot.pipeline if stage.key == "review")
    assert review.state == "attention"
    assert snapshot.attention
    assert snapshot.attention[0].route == AppRoute.TRANSCRIPT
    assert snapshot.provider == "Локальная LLM"


def test_cockpit_and_context_render_empty_and_active_states() -> None:
    _application()
    page = TeacherCockpitPage()
    context = GlobalContextBar()
    snapshot = build_cockpit_snapshot(_window_stub(), route=AppRoute.TRANSCRIPT)

    page.set_snapshot(snapshot)
    context.set_snapshot(snapshot)

    assert page.pipeline.buttons["review"].property("state") == "attention"
    assert "Софья Кальной" in context.breadcrumb.text()
    assert "Метод интервалов" in context.detail.text()
    assert page.attention_count.text() == str(len(snapshot.attention))


def test_command_palette_filters_and_executes_keyboard_command() -> None:
    application = _application()
    palette = CommandPalette()
    executed: list[str] = []
    palette.open_with_commands(
        [
            PaletteCommand(
                "route:today",
                "Сегодня",
                "Открыть обзор",
                lambda: executed.append("today"),
                ("день",),
                "Ctrl+0",
            ),
            PaletteCommand(
                "route:students",
                "Ученики",
                "Открыть карточки",
                lambda: executed.append("students"),
                ("crm",),
                "Ctrl+6",
            ),
        ]
    )
    palette.search.setText("учен")
    application.processEvents()

    assert palette.results.count() == 1
    assert "Ученики" in palette.results.item(0).text()
    QTest.keyClick(palette, Qt.Key.Key_Return)
    assert executed == ["students"]


def test_ui_session_round_trip_route_sidebar_geometry_and_splitter(tmp_path) -> None:
    _application()
    settings = QSettings(str(tmp_path / "ux6.ini"), QSettings.Format.IniFormat)
    first = QMainWindow()
    first.resize(920, 680)
    splitter = QSplitter()
    splitter.addWidget(QWidget())
    splitter.addWidget(QWidget())
    splitter.setSizes([620, 300])
    store = UISessionStore(
        first,
        settings=settings,
        route_provider=lambda: AppRoute.MATERIALS,
    )
    store.register_splitter("test", splitter)
    store.record_mode("detailed")
    store.record_sidebar_collapsed(True)
    store.save_all()

    second = QMainWindow()
    restored_splitter = QSplitter()
    restored_splitter.addWidget(QWidget())
    restored_splitter.addWidget(QWidget())
    restored = UISessionStore(second, settings=settings)
    restored.register_splitter("test", restored_splitter)
    restored.restore_window()
    restored.restore_splitters()

    assert restored.initialized
    assert restored.preferred_route() == AppRoute.MATERIALS
    assert restored.preferred_mode("quick") == "detailed"
    assert restored.sidebar_collapsed() is True
    assert sum(restored_splitter.sizes()) > 0
