from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSplitter,
    QTabWidget,
    QWidget,
)

from tutor_assistant.crm import CrmStats, ScheduledLesson
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.ui.app_routes import (
    ROUTE_DEFINITIONS,
    AppRoute,
    page_for_route,
    route_for_page,
)
from tutor_assistant.ui.command_palette import CommandPalette, PaletteCommand
from tutor_assistant.ui.information_architecture import SidebarNavigation
from tutor_assistant.ui.teacher_cockpit import (
    GlobalContextBar,
    TeacherCockpitPage,
    build_cockpit_snapshot,
    format_dashboard_timestamp,
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


def _lesson(
    status: JobStatus = JobStatus.REVIEW_REQUIRED,
    *,
    student_id: str = "sofya",
    name: str = "Софья Кальной",
    topic: str = "Метод интервалов",
) -> Lesson:
    return Lesson(
        student=Student(id=student_id, full_name=name),
        subject="mathematics",
        lesson_date=date(2026, 8, 1),
        topic=topic,
        status=status,
    )


class _LessonStore:
    def __init__(self, lessons: list[Lesson]) -> None:
        self.lessons = lessons

    def list(self, limit: int = 100) -> list[Lesson]:
        return self.lessons[:limit]


class _CrmStore:
    def __init__(
        self,
        lessons_by_week: dict[date, list[ScheduledLesson]],
        *,
        error: Exception | None = None,
    ) -> None:
        self.lessons_by_week = lessons_by_week
        self.error = error

    def lessons_for_week(self, week_start: date) -> list[ScheduledLesson]:
        if self.error is not None:
            raise self.error
        return list(self.lessons_by_week.get(week_start, []))

    def stats(self, week_start: date) -> CrmStats:
        if self.error is not None:
            raise self.error
        lessons = self.lessons_by_week.get(week_start, [])
        return CrmStats(1, len(lessons), sum(item.rate_cents for item in lessons))


def _window_stub(
    status: JobStatus = JobStatus.REVIEW_REQUIRED,
    *,
    stored_lessons: list[Lesson] | None = None,
    crm_store: object | None = None,
):
    lesson = _lesson(status)
    return SimpleNamespace(
        lesson=lesson,
        students=[lesson.student],
        workers=[],
        crm_store=crm_store,
        pipeline=SimpleNamespace(
            store=_LessonStore(stored_lessons or [lesson]),
        ),
        config=SimpleNamespace(
            normalization=SimpleNamespace(provider="ollama"),
        ),
    )


def test_route_registry_preserves_legacy_indices() -> None:
    assert page_for_route(AppRoute.LESSON) == 0
    assert page_for_route(AppRoute.MATERIALS) == 7
    assert page_for_route(AppRoute.TODAY) == 8
    assert route_for_page(3) == AppRoute.LATEX
    shortcuts = {definition.shortcut for definition in ROUTE_DEFINITIONS}
    assert len(shortcuts) == len(ROUTE_DEFINITIONS)


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
    review = next(
        stage
        for stage in snapshot.pipeline
        if stage.key == "review"
    )
    assert review.state == "attention"
    assert snapshot.attention
    assert snapshot.attention[0].route == AppRoute.TRANSCRIPT
    assert snapshot.provider == "Локальная LLM"


def test_attention_aggregates_all_unfinished_stored_lessons() -> None:
    review = _lesson(
        JobStatus.REVIEW_REQUIRED,
        student_id="review",
        name="Анна",
        topic="Неравенства",
    )
    ready = _lesson(
        JobStatus.READY,
        student_id="ready",
        name="Иван",
        topic="Производная",
    )
    failed = _lesson(
        JobStatus.FAILED,
        student_id="failed",
        name="Яна",
        topic="Тригонометрия",
    )
    window = _window_stub(
        JobStatus.DRAFT,
        stored_lessons=[review, ready, failed],
    )
    window.lesson = None

    snapshot = build_cockpit_snapshot(
        window,
        now=datetime(2026, 8, 1, 17, 0),
    )

    routes = {item.route for item in snapshot.attention}
    assert AppRoute.TRANSCRIPT in routes
    assert AppRoute.PUBLICATION in routes
    assert AppRoute.PROCESSING in routes
    keys = {item.key for item in snapshot.attention}
    assert f"review-{review.lesson_id}" in keys
    assert f"publish-{ready.lesson_id}" in keys
    assert f"failed-{failed.lesson_id}" in keys


def test_next_lesson_crosses_calendar_week_boundary() -> None:
    sunday = datetime(2026, 8, 2, 20, 0)
    current_monday = date(2026, 7, 27)
    next_monday = date(2026, 8, 3)
    scheduled = ScheduledLesson(
        student_id="sofya",
        student_name="Софья Кальной",
        starts_at=datetime(2026, 8, 3, 10, 0),
        duration_minutes=60,
        subject="mathematics",
        topic="Геометрия",
    )
    crm = _CrmStore(
        {
            current_monday: [],
            next_monday: [scheduled],
        }
    )

    snapshot = build_cockpit_snapshot(
        _window_stub(crm_store=crm),
        now=sunday,
    )

    assert snapshot.next_lesson == scheduled
    assert snapshot.minutes_to_next == 14 * 60


def test_crm_failure_becomes_attention_item() -> None:
    crm = _CrmStore({}, error=RuntimeError("database is locked"))

    snapshot = build_cockpit_snapshot(
        _window_stub(crm_store=crm),
        now=datetime(2026, 8, 1, 17, 0),
    )

    assert snapshot.crm_error == "database is locked"
    assert any(
        item.key == "crm-unavailable"
        and item.severity == "critical"
        for item in snapshot.attention
    )


def test_cockpit_and_context_render_empty_and_active_states() -> None:
    _application()
    page = TeacherCockpitPage()
    context = GlobalContextBar()
    snapshot = build_cockpit_snapshot(
        _window_stub(),
        route=AppRoute.TRANSCRIPT,
        now=datetime(2026, 8, 1, 15, 10),
    )

    page.set_snapshot(snapshot)
    context.set_snapshot(snapshot)

    assert page.pipeline.buttons["review"].property("state") == "attention"
    assert "Софья Кальной" in context.breadcrumb.text()
    assert "Метод интервалов" in context.detail.text()
    assert page.attention_count.text() == str(len(snapshot.attention))
    assert page.subtitle.text() == (
        "Суббота, 01.08.2026 · обновлено 15:10"
    )


def test_dashboard_timestamp_is_locale_independent() -> None:
    value = datetime(2026, 8, 1, 15, 10)
    assert format_dashboard_timestamp(value) == (
        "Суббота, 01.08.2026 · обновлено 15:10"
    )


def test_pipeline_refresh_preserves_button_and_focus() -> None:
    application = _application()
    page = TeacherCockpitPage()
    page.show()
    snapshot = build_cockpit_snapshot(
        _window_stub(),
        now=datetime(2026, 8, 1, 15, 10),
    )
    page.set_snapshot(snapshot)
    button = page.pipeline.buttons["review"]
    button.setFocus()
    application.processEvents()

    page.set_snapshot(snapshot)
    application.processEvents()

    assert page.pipeline.buttons["review"] is button
    assert QApplication.focusWidget() is button
    page.close()


def test_command_palette_works_from_search_and_results_focus() -> None:
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
    application.processEvents()
    assert QApplication.focusWidget() is palette.search

    QTest.keyClick(palette.search, Qt.Key.Key_Down)
    application.processEvents()
    assert QApplication.focusWidget() is palette.results
    assert palette.results.currentRow() == 1

    QTest.keyClick(palette.results, Qt.Key.Key_Return)
    assert executed == ["students"]


def test_command_palette_filters_and_escape_closes_from_search() -> None:
    application = _application()
    palette = CommandPalette()
    palette.open_with_commands(
        [
            PaletteCommand(
                "route:students",
                "Ученики",
                "Открыть карточки",
                lambda: None,
                ("crm",),
            ),
        ]
    )
    palette.search.setText("учен")
    application.processEvents()

    assert palette.results.count() == 1
    QTest.keyClick(palette.search, Qt.Key.Key_Escape)
    application.processEvents()
    assert not palette.isVisible()


def test_ui_session_round_trip_route_sidebar_geometry_and_splitter(
    tmp_path,
) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "ux6.ini"),
        QSettings.Format.IniFormat,
    )
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


def test_ui_session_rejects_incompatible_schema(tmp_path) -> None:
    _application()
    settings = QSettings(
        str(tmp_path / "ux6-old.ini"),
        QSettings.Format.IniFormat,
    )
    settings.setValue("ux6/schema", 999)
    settings.setValue("ux6/initialized", True)
    settings.setValue("ux6/last_route", AppRoute.MATERIALS.value)
    window = QMainWindow()
    store = UISessionStore(window, settings=settings)

    store.restore_window()

    assert not store.initialized
    assert store.preferred_route(AppRoute.TODAY) == AppRoute.TODAY
    assert settings.value("ux6/schema") is None


def test_ui_session_moves_offscreen_window_back_to_available_screen(
    tmp_path,
) -> None:
    application = _application()
    settings = QSettings(
        str(tmp_path / "ux6-screen.ini"),
        QSettings.Format.IniFormat,
    )
    first = QMainWindow()
    first.setGeometry(100_000, 100_000, 900, 700)
    first_store = UISessionStore(first, settings=settings)
    first_store.save_all()

    second = QMainWindow()
    second_store = UISessionStore(second, settings=settings)
    second_store.restore_window()
    application.processEvents()

    available = [
        screen.availableGeometry()
        for screen in QGuiApplication.screens()
    ]
    assert available
    assert any(
        rect.intersects(second.frameGeometry())
        for rect in available
    )
