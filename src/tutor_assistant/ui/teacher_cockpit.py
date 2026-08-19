from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QSplitter

from ..application import WorkspaceContextCoordinator, WorkspaceContextSnapshot
from .app_routes import ROUTE_DEFINITIONS, AppRoute
from .command_palette import CommandPalette, PaletteCommand
from .lesson_journal_integration import install_lesson_journal
from .teacher_cockpit_data import (
    AttentionItem,
    CockpitDataInputs,
    CockpitSnapshot,
    PipelineStage,
    build_cockpit_snapshot,
    collect_cockpit_inputs,
    format_dashboard_timestamp,
)
from .teacher_cockpit_widgets import (
    COCKPIT_STYLESHEET,
    GlobalContextBar,
    TeacherCockpitPage,
)
from .ui_session import UISessionStore

__all__ = [
    "AttentionItem",
    "CockpitSnapshot",
    "GlobalContextBar",
    "PipelineStage",
    "TeacherCockpitPage",
    "build_cockpit_snapshot",
    "format_dashboard_timestamp",
    "install_teacher_cockpit",
]

_build_cockpit_snapshot = build_cockpit_snapshot


def _legacy_workspace_snapshot(window: Any) -> WorkspaceContextSnapshot:
    provider = getattr(window, "workspace_context_snapshot", None)
    if callable(provider):
        return provider()
    coordinator = WorkspaceContextCoordinator()
    recorder = getattr(window, "recorder", None)
    return coordinator.sync(
        recording_lesson=getattr(window, "recording_lesson", None),
        review_lesson=getattr(window, "lesson", None),
        recording_active=bool(recorder and getattr(recorder, "active", False)),
        recording_stopping=bool(getattr(window, "_recording_stop_started", False)),
        elapsed_seconds=int(getattr(window, "recording_seconds", 0)),
    )


def build_cockpit_snapshot(
    source: CockpitDataInputs | Any,
    *,
    route: AppRoute = AppRoute.TODAY,
    now: datetime | None = None,
) -> CockpitSnapshot:
    """Compatibility adapter for older direct callers.

    Production synchronization does not use this adapter: it builds explicit
    ``CockpitDataInputs`` inside ``TeacherCockpitController`` and calls the pure
    data-layer builder. The wrapper remains for stable tests/extensions while
    they migrate away from passing an entire Qt window.
    """

    if isinstance(source, CockpitDataInputs):
        return _build_cockpit_snapshot(source, route=route)
    inputs = collect_cockpit_inputs(
        workspace=_legacy_workspace_snapshot(source),
        crm_store=getattr(source, "crm_store", None),
        lesson_store=getattr(getattr(source, "pipeline", None), "store", None),
        active_students=len(getattr(source, "students", ()) or ()),
        workers=tuple(getattr(source, "workers", ()) or ()),
        provider_value=str(
            getattr(
                getattr(getattr(source, "config", None), "normalization", None),
                "provider",
                "ollama",
            )
        ),
        now=now,
    )
    return _build_cockpit_snapshot(inputs, route=route)


class TeacherCockpitController(QObject):
    """Coordinates the UX-6 layer around explicit workspace/query ports."""

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.navigation: Any | None = None
        self.current_route = AppRoute.TODAY
        self.dashboard = TeacherCockpitPage()
        self.context_bar = GlobalContextBar()
        self.palette = CommandPalette(window)
        self.session: UISessionStore | None = None
        self.last_snapshot = self._snapshot()
        self.dashboard_index = self.window.tabs.addTab(self.dashboard, "09  Сегодня")
        if self.dashboard_index != 8:
            raise RuntimeError("Экран «Сегодня» должен сохранять legacy-индексы 0–7")
        self.journal = install_lesson_journal(self.window)
        self.window.centralWidget().layout().insertWidget(1, self.context_bar)

        self.dashboard.route_requested.connect(self.navigate)
        self.dashboard.quick_requested.connect(lambda: self.window._set_mode("quick"))
        self.dashboard.refresh_requested.connect(self.refresh)
        self.context_bar.route_requested.connect(self.navigate)
        self.context_bar.refresh_requested.connect(self.refresh)

        # Defensive fallback for changes outside the local event stream.
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(30_000)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()

        self.palette_shortcut = self._shortcut("Ctrl+K", self.open_palette)
        self.refresh_shortcut = self._shortcut("F5", self.refresh)
        self.route_shortcuts: list[QShortcut] = []
        _install_stylesheet()

    def _shortcut(self, sequence: str, callback: Any) -> QShortcut:
        shortcut = QShortcut(QKeySequence(sequence), self.window)
        shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        shortcut.activated.connect(callback)
        return shortcut

    def _workspace_snapshot(self) -> WorkspaceContextSnapshot:
        provider = getattr(self.window, "workspace_context_snapshot", None)
        if callable(provider):
            return provider()
        return _legacy_workspace_snapshot(self.window)

    def _snapshot(
        self,
        *,
        workspace: WorkspaceContextSnapshot | None = None,
        route: AppRoute | None = None,
        now: datetime | None = None,
    ) -> CockpitSnapshot:
        workspace = workspace or self._workspace_snapshot()
        inputs = collect_cockpit_inputs(
            workspace=workspace,
            crm_store=self.window.crm_store,
            lesson_store=self.window.pipeline.store,
            active_students=len(self.window.students),
            workers=tuple(self.window.workers),
            provider_value=self.window.config.normalization.provider,
            now=now,
        )
        return _build_cockpit_snapshot(
            inputs,
            route=route or self.current_route,
        )

    def bind_navigation(self, navigation: Any) -> None:
        self.navigation = navigation
        self.current_route = navigation.current_route()
        navigation.route_changed.connect(self._route_changed)
        navigation.command_palette_requested.connect(self.open_palette)
        self.session = UISessionStore(self.window, route_provider=navigation.current_route)
        self.session.restore_window()
        materials = getattr(self.window.student_content_page, "content_splitter", None)
        students = self.window.crm_students_page.findChild(QSplitter)
        journal = self.journal.findChild(QSplitter)
        self.session.register_splitter("materials", materials)
        self.session.register_splitter("students", students)
        self.session.register_splitter("journal", journal)
        navigation.set_collapsed(self.session.sidebar_collapsed())
        navigation.collapsed_changed.connect(self.session.record_sidebar_collapsed)
        navigation.route_changed.connect(self.session.record_route)
        self.session.restore_deferred(self.window.student_content_page)
        for definition in ROUTE_DEFINITIONS:
            shortcut = self._shortcut(
                definition.shortcut,
                lambda route=definition.route: self.navigate(route.value),
            )
            self.route_shortcuts.append(shortcut)
        self.refresh()

    def restore_session(self, default_mode: str) -> None:
        if self.session is None or self.navigation is None:
            self.window._set_mode(default_mode)
            return
        mode = self.session.preferred_mode(default_mode) if self.session.initialized else default_mode
        if mode == "quick":
            self.window._set_mode("quick")
        else:
            self.window._set_mode("detailed")
            self.navigation.navigate(self.session.preferred_route())
        self.session.mark_initialized()

    def _route_changed(self, route_value: str) -> None:
        self.current_route = AppRoute(route_value)
        self.refresh()

    def navigate(self, route_value: str) -> None:
        route = AppRoute(route_value)
        if route == AppRoute.QUICK_LESSON:
            self.window._set_mode("quick")
            return
        self.window._set_mode("detailed")
        if self.navigation is not None:
            self.navigation.navigate(route)

    def _student_command(self, student: object) -> None:
        self.navigate(AppRoute.STUDENTS.value)
        search = getattr(self.window.crm_students_page, "search", None)
        if search is not None:
            search.setText(str(getattr(student, "full_name", "")))
            search.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _journal_command(self, view: str) -> None:
        self.journal.apply_smart_view(view)
        self.navigate(AppRoute.JOURNAL.value)

    def commands(self) -> list[PaletteCommand]:
        commands = [
            PaletteCommand(
                f"route:{definition.route.value}",
                definition.title,
                f"Открыть раздел · {definition.group.title()}",
                lambda route=definition.route: self.navigate(route.value),
                definition.keywords,
                definition.shortcut,
            )
            for definition in ROUTE_DEFINITIONS
        ]
        for student in getattr(self.window, "students", ()):
            student_id = str(getattr(student, "id", ""))
            commands.append(
                PaletteCommand(
                    f"student:{student_id}",
                    str(getattr(student, "full_name", student_id)),
                    "Открыть карточку ученика",
                    lambda current=student: self._student_command(current),
                    (student_id, "ученик", "карточка"),
                )
            )
        if self.last_snapshot.next_lesson is not None:
            lesson = self.last_snapshot.next_lesson
            commands.append(
                PaletteCommand(
                    "next-lesson",
                    f"Следующее занятие: {lesson.student_name}",
                    lesson.starts_at.strftime("%d.%m · %H:%M"),
                    lambda: self.navigate(AppRoute.SCHEDULE.value),
                    ("следующее", "расписание", lesson.student_name),
                )
            )
        commands.extend(
            (
                PaletteCommand(
                    "journal:attention",
                    "Занятия, требующие внимания",
                    "Долги, просроченное ДЗ и незавершённые статусы",
                    lambda: self._journal_command("attention"),
                    ("журнал", "внимание", "долги", "дз"),
                ),
                PaletteCommand(
                    "journal:unpaid",
                    "Неоплаченные занятия",
                    "Открыть прошедшие занятия с задолженностью",
                    lambda: self._journal_command("unpaid"),
                    ("журнал", "оплата", "долг", "финансы"),
                ),
                PaletteCommand(
                    "journal:homework-review",
                    "ДЗ на проверку",
                    "Показать полученные домашние работы",
                    lambda: self._journal_command("homework_review"),
                    ("журнал", "дз", "домашняя работа", "проверка"),
                ),
                PaletteCommand(
                    "system:refresh",
                    "Обновить Teacher Cockpit",
                    "Пересчитать расписание, pipeline и центр внимания",
                    self.refresh,
                    ("refresh", "обновить", "состояние"),
                    "F5",
                ),
                PaletteCommand(
                    "system:diagnostics",
                    "Собрать диагностический пакет",
                    "Создать безопасный ZIP без аудио и транскриптов",
                    self.window._create_support_bundle,
                    ("диагностика", "support", "zip"),
                ),
                PaletteCommand(
                    "system:logs",
                    "Открыть журнал приложения",
                    "Показать каталог локальных журналов",
                    self.window._open_logs,
                    ("логи", "журнал", "ошибки"),
                ),
                PaletteCommand(
                    "system:llm-settings",
                    "Настройки LLM-фильтрации",
                    "Провайдер, модель и параметры повторных запросов",
                    self.window._show_normalization_settings,
                    ("ollama", "yandex", "модель", "настройки"),
                ),
            )
        )
        return commands

    def open_palette(self) -> None:
        self.palette.open_with_commands(self.commands())

    def refresh(
        self,
        *,
        workspace: WorkspaceContextSnapshot | None = None,
    ) -> None:
        route = self.navigation.current_route() if self.navigation is not None else self.current_route
        self.current_route = route
        self.last_snapshot = self._snapshot(workspace=workspace, route=route)
        self.dashboard.set_snapshot(self.last_snapshot)
        self.context_bar.set_snapshot(self.last_snapshot)
        if self.navigation is not None:
            self.navigation.set_badges(Counter(item.route for item in self.last_snapshot.attention))


def install_teacher_cockpit(window: Any) -> TeacherCockpitController:
    return TeacherCockpitController(window)


def _install_stylesheet() -> None:
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        return
    if application.property("ux6TeacherCockpitStyle"):
        return
    application.setStyleSheet(application.styleSheet() + COCKPIT_STYLESHEET)
    application.setProperty("ux6TeacherCockpitStyle", True)
