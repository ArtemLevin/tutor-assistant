from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..crm import CrmStats, ScheduledLesson
from ..domain import JobStatus, Lesson
from .app_routes import ROUTE_DEFINITIONS, AppRoute, route_definition
from .command_palette import CommandPalette, PaletteCommand
from .localization import subject_label
from .theme import refresh_style, set_button_kind
from .ui_session import UISessionStore

COCKPIT_STYLESHEET = """
QFrame#globalContextBar,
QFrame#cockpitHero,
QFrame#cockpitCard,
QFrame#pipelineCard,
QFrame#attentionCard {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 14px;
}
QFrame#globalContextBar {
    min-height: 50px;
}
QLabel#contextBreadcrumb {
    color: #344054;
    font-size: 13px;
    font-weight: 700;
}
QLabel#contextDetail {
    color: #667085;
    font-size: 12px;
}
QLabel#cockpitHeroTitle {
    color: #101828;
    font-size: 23px;
    font-weight: 750;
}
QLabel#cockpitHeroTime {
    color: #275AA6;
    font-size: 15px;
    font-weight: 700;
}
QLabel#cockpitMetricValue {
    color: #101828;
    font-size: 22px;
    font-weight: 750;
}
QLabel#cockpitMetricLabel {
    color: #667085;
    font-size: 11px;
}
QPushButton#pipelineStage {
    min-height: 48px;
    padding: 7px 10px;
    text-align: left;
    border-radius: 10px;
    border: 1px solid #D8E0EA;
    background: #F8FAFC;
    color: #526174;
    font-weight: 650;
}
QPushButton#pipelineStage[state="completed"] {
    background: #EAF8F1;
    border-color: #B8E4CF;
    color: #236B4A;
}
QPushButton#pipelineStage[state="active"] {
    background: #EAF2FF;
    border-color: #BFD5F6;
    color: #275AA6;
}
QPushButton#pipelineStage[state="attention"] {
    background: #FFF4D6;
    border-color: #E9CC7A;
    color: #845800;
}
QPushButton#pipelineStage:focus {
    border: 2px solid #4D7FD6;
}
QListWidget#attentionList {
    border: 0;
    background: transparent;
    outline: 0;
}
"""


@dataclass(frozen=True, slots=True)
class PipelineStage:
    key: str
    title: str
    route: AppRoute
    state: str
    detail: str


@dataclass(frozen=True, slots=True)
class AttentionItem:
    key: str
    severity: str
    route: AppRoute
    title: str
    detail: str


@dataclass(frozen=True, slots=True)
class CockpitSnapshot:
    created_at: datetime
    route: AppRoute
    lesson: Lesson | None
    next_lesson: ScheduledLesson | None
    minutes_to_next: int | None
    stats: CrmStats
    active_students: int
    background_jobs: int
    provider: str
    pipeline: tuple[PipelineStage, ...]
    attention: tuple[AttentionItem, ...]
    crm_error: str | None = None
    lesson_store_error: str | None = None


_STATUS_TITLES = {
    JobStatus.DRAFT: "Черновик занятия",
    JobStatus.RECORDING: "Идёт запись",
    JobStatus.RECORDED: "Аудио сохранено",
    JobStatus.TRANSCRIBING: "Идёт транскрибация",
    JobStatus.REVIEW_REQUIRED: "Требуется проверка транскрипта",
    JobStatus.READY: "Транскрипт готов к публикации",
    JobStatus.PUBLISHED: "Транскрипт опубликован",
    JobStatus.GENERATED_TEX: "Исходник LaTeX подготовлен",
    JobStatus.COMPILING_PDF: "Идёт компиляция PDF",
    JobStatus.PDF_REVIEW_REQUIRED: "Требуется проверка PDF",
    JobStatus.COMPILE_FAILED: "Ошибка компиляции PDF",
    JobStatus.GENERATING: "Формируются материалы",
    JobStatus.COMPLETED: "Занятие полностью обработано",
    JobStatus.FAILED: "Обработка остановлена с ошибкой",
}
_RUSSIAN_WEEKDAYS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)


def format_dashboard_timestamp(value: datetime) -> str:
    weekday = _RUSSIAN_WEEKDAYS[value.weekday()]
    return f"{weekday}, {value:%d.%m.%Y} · обновлено {value:%H:%M}"


def _safe_running_workers(window: object) -> int:
    count = 0
    for worker in getattr(window, "workers", ()):
        try:
            running = bool(worker.isRunning())
        except (AttributeError, RuntimeError):
            running = False
        count += int(running)
    return count


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _scheduled_context(
    window: object,
    now: datetime,
) -> tuple[list[ScheduledLesson], CrmStats, str | None]:
    store = getattr(window, "crm_store", None)
    if store is None:
        return [], CrmStats(0, 0, 0), None
    monday = _week_start(now.date())
    try:
        current_week = list(store.lessons_for_week(monday))
        next_week = list(store.lessons_for_week(monday + timedelta(days=7)))
        stats = store.stats(monday)
        return current_week + next_week, stats, None
    except Exception as exc:
        logging.exception("Teacher Cockpit: CRM data unavailable")
        return [], CrmStats(0, 0, 0), str(exc) or type(exc).__name__


def _stored_lessons(window: object) -> tuple[list[Lesson], str | None]:
    pipeline = getattr(window, "pipeline", None)
    store = getattr(pipeline, "store", None)
    list_lessons = getattr(store, "list", None)
    if not callable(list_lessons):
        return [], None
    try:
        try:
            lessons = list(list_lessons(limit=250))
        except TypeError:
            lessons = list(list_lessons())
    except Exception as exc:
        logging.exception("Teacher Cockpit: lesson store unavailable")
        return [], str(exc) or type(exc).__name__

    current = getattr(window, "lesson", None)
    if isinstance(current, Lesson):
        lessons.append(current)
    unique: dict[str, Lesson] = {}
    for lesson in lessons:
        if isinstance(lesson, Lesson):
            unique[lesson.lesson_id] = lesson
    return list(unique.values()), None


def _next_scheduled_lesson(
    lessons: list[ScheduledLesson],
    now: datetime,
) -> tuple[ScheduledLesson | None, int | None]:
    candidates = [
        item
        for item in lessons
        if item.status != "cancelled" and item.ends_at >= now
    ]
    if not candidates:
        return None, None
    selected = min(candidates, key=lambda item: item.starts_at)
    minutes = round((selected.starts_at - now).total_seconds() / 60)
    return selected, minutes


def _pipeline_for_lesson(lesson: Lesson | None) -> tuple[PipelineStage, ...]:
    stage_data = (
        ("prepare", "Подготовка", AppRoute.LESSON),
        ("record", "Запись", AppRoute.LESSON),
        ("transcribe", "Транскрипция", AppRoute.PROCESSING),
        ("review", "Проверка", AppRoute.TRANSCRIPT),
        ("publish", "Публикация", AppRoute.PUBLICATION),
        ("materials", "Материалы", AppRoute.LATEX),
    )
    if lesson is None:
        return tuple(
            PipelineStage(key, title, route, "pending", "Занятие пока не выбрано")
            for key, title, route in stage_data
        )

    status = lesson.status
    completed: set[str] = set()
    active = "prepare"
    attention: set[str] = set()

    if status == JobStatus.RECORDING:
        completed.add("prepare")
        active = "record"
    elif status in {JobStatus.RECORDED, JobStatus.TRANSCRIBING}:
        completed.update({"prepare", "record"})
        active = "transcribe"
    elif status == JobStatus.REVIEW_REQUIRED:
        completed.update({"prepare", "record", "transcribe"})
        active = "review"
        attention.add("review")
    elif status == JobStatus.READY:
        completed.update({"prepare", "record", "transcribe", "review"})
        active = "publish"
    elif status == JobStatus.PUBLISHED:
        completed.update({"prepare", "record", "transcribe", "review", "publish"})
        active = "materials"
    elif status in {
        JobStatus.GENERATED_TEX,
        JobStatus.COMPILING_PDF,
        JobStatus.GENERATING,
    }:
        completed.update({"prepare", "record", "transcribe", "review", "publish"})
        active = "materials"
    elif status in {JobStatus.PDF_REVIEW_REQUIRED, JobStatus.COMPILE_FAILED}:
        completed.update({"prepare", "record", "transcribe", "review", "publish"})
        active = "materials"
        attention.add("materials")
    elif status == JobStatus.COMPLETED:
        completed.update(key for key, _title, _route in stage_data)
        active = ""
    elif status == JobStatus.FAILED:
        active = "transcribe" if lesson.source_audio_local else "prepare"
        attention.add(active)

    detail = _STATUS_TITLES.get(status, status.value)
    stages: list[PipelineStage] = []
    for key, title, route in stage_data:
        state = "completed" if key in completed else "pending"
        if key == active:
            state = "attention" if key in attention else "active"
        stages.append(PipelineStage(key, title, route, state, detail))
    return tuple(stages)


def _lesson_attention(lesson: Lesson) -> AttentionItem | None:
    topic = lesson.topic or subject_label(lesson.subject)
    detail = f"{lesson.student.full_name} · {topic}"
    if lesson.status == JobStatus.REVIEW_REQUIRED:
        return AttentionItem(
            f"review-{lesson.lesson_id}",
            "warning",
            AppRoute.TRANSCRIPT,
            "Проверьте транскрипт",
            detail,
        )
    if lesson.status == JobStatus.READY:
        return AttentionItem(
            f"publish-{lesson.lesson_id}",
            "warning",
            AppRoute.PUBLICATION,
            "Транскрипт готов к публикации",
            detail,
        )
    if lesson.status == JobStatus.PDF_REVIEW_REQUIRED:
        return AttentionItem(
            f"pdf-{lesson.lesson_id}",
            "warning",
            AppRoute.LATEX,
            "Проверьте собранный PDF",
            detail,
        )
    if lesson.status in {JobStatus.FAILED, JobStatus.COMPILE_FAILED}:
        route = (
            AppRoute.LATEX
            if lesson.status == JobStatus.COMPILE_FAILED
            else AppRoute.PROCESSING
        )
        return AttentionItem(
            f"failed-{lesson.lesson_id}",
            "critical",
            route,
            "Обработка занятия остановлена",
            lesson.error or detail,
        )
    return None


def _attention_items(
    window: object,
    stored_lessons: list[Lesson],
    scheduled_lessons: list[ScheduledLesson],
    now: datetime,
    background_jobs: int,
    *,
    crm_error: str | None,
    lesson_store_error: str | None,
) -> tuple[AttentionItem, ...]:
    items: list[AttentionItem] = []

    if not getattr(window, "students", None) and not stored_lessons:
        items.append(
            AttentionItem(
                "no-students",
                "info",
                AppRoute.STUDENTS,
                "Создайте карточку первого ученика",
                "После этого станут доступны расписание и быстрый запуск занятия.",
            )
        )

    for lesson in stored_lessons:
        item = _lesson_attention(lesson)
        if item is not None:
            items.append(item)

    overdue = [
        lesson
        for lesson in scheduled_lessons
        if lesson.status == "planned" and lesson.ends_at < now
    ]
    for scheduled in overdue[-3:]:
        items.append(
            AttentionItem(
                (
                    f"overdue-{scheduled.occurrence_id or scheduled.rule_id}-"
                    f"{scheduled.starts_at.isoformat()}"
                ),
                "warning",
                AppRoute.SCHEDULE,
                f"Уточните статус занятия с {scheduled.student_name}",
                scheduled.starts_at.strftime("%d.%m · %H:%M"),
            )
        )

    if crm_error:
        items.append(
            AttentionItem(
                "crm-unavailable",
                "critical",
                AppRoute.SCHEDULE,
                "Данные расписания временно недоступны",
                crm_error,
            )
        )
    if lesson_store_error:
        items.append(
            AttentionItem(
                "lesson-store-unavailable",
                "critical",
                AppRoute.MATERIALS,
                "История занятий временно недоступна",
                lesson_store_error,
            )
        )
    if background_jobs:
        items.append(
            AttentionItem(
                "background-jobs",
                "info",
                AppRoute.PROCESSING,
                f"Выполняются фоновые задачи: {background_jobs}",
                "Откройте очередь для просмотра подробного прогресса.",
            )
        )

    unique: dict[str, AttentionItem] = {}
    for item in items:
        unique.setdefault(item.key, item)
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                severity_order.get(item.severity, 3),
                item.title,
                item.key,
            ),
        )[:12]
    )


def build_cockpit_snapshot(
    window: object,
    *,
    route: AppRoute = AppRoute.TODAY,
    now: datetime | None = None,
) -> CockpitSnapshot:
    current_time = now or datetime.now()
    scheduled_lessons, stats, crm_error = _scheduled_context(window, current_time)
    next_lesson, minutes = _next_scheduled_lesson(
        scheduled_lessons,
        current_time,
    )
    stored_lessons, lesson_store_error = _stored_lessons(window)

    lesson = getattr(window, "lesson", None)
    if not isinstance(lesson, Lesson):
        lesson = None
    background_jobs = _safe_running_workers(window)
    provider_value = getattr(
        getattr(
            getattr(window, "config", None),
            "normalization",
            None,
        ),
        "provider",
        "ollama",
    )
    provider = (
        "Yandex AI Studio"
        if provider_value == "yandex_ai_studio"
        else "Локальная LLM"
    )
    attention = _attention_items(
        window,
        stored_lessons,
        scheduled_lessons,
        current_time,
        background_jobs,
        crm_error=crm_error,
        lesson_store_error=lesson_store_error,
    )
    return CockpitSnapshot(
        created_at=current_time,
        route=route,
        lesson=lesson,
        next_lesson=next_lesson,
        minutes_to_next=minutes,
        stats=stats,
        active_students=len(getattr(window, "students", ()) or ()),
        background_jobs=background_jobs,
        provider=provider,
        pipeline=_pipeline_for_lesson(lesson),
        attention=attention,
        crm_error=crm_error,
        lesson_store_error=lesson_store_error,
    )


class LessonPipelineWidget(QFrame):
    route_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pipelineCard")
        self.setAccessibleName("Этапы обработки занятия")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        title = QLabel("Жизненный цикл занятия")
        title.setObjectName("tileTitle")
        layout.addWidget(title)
        self.stage_layout = QHBoxLayout()
        self.stage_layout.setSpacing(7)
        layout.addLayout(self.stage_layout)
        self.buttons: dict[str, QPushButton] = {}

    def _create_button(self, key: str) -> QPushButton:
        button = QPushButton()
        button.setObjectName("pipelineStage")
        button.clicked.connect(
            lambda _checked=False, current=button: self.route_requested.emit(
                str(current.property("route"))
            )
        )
        self.stage_layout.addWidget(button, 1)
        self.buttons[key] = button
        return button

    def set_stages(self, stages: tuple[PipelineStage, ...]) -> None:
        current_keys = {stage.key for stage in stages}
        for key in tuple(self.buttons):
            if key in current_keys:
                continue
            button = self.buttons.pop(key)
            self.stage_layout.removeWidget(button)
            button.deleteLater()

        state_icons = {
            "completed": "✓",
            "active": "●",
            "attention": "!",
            "pending": "○",
        }
        state_titles = {
            "completed": "завершено",
            "active": "выполняется",
            "attention": "требует внимания",
            "pending": "ожидает",
        }
        for stage in stages:
            button = self.buttons.get(stage.key) or self._create_button(stage.key)
            button.setText(f"{state_icons.get(stage.state, '○')}  {stage.title}")
            button.setProperty("state", stage.state)
            button.setProperty("route", stage.route.value)
            button.setToolTip(stage.detail)
            button.setAccessibleName(
                f"{stage.title}: {state_titles.get(stage.state, stage.state)}"
            )
            button.setAccessibleDescription(stage.detail)
            refresh_style(button)


class GlobalContextBar(QFrame):
    refresh_requested = Signal()
    route_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("globalContextBar")
        self.setAccessibleName("Текущий контекст Tutor Assistant")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 9, 12, 9)
        layout.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.breadcrumb = QLabel("Сегодня")
        self.breadcrumb.setObjectName("contextBreadcrumb")
        self.detail = QLabel("Рабочее пространство готово")
        self.detail.setObjectName("contextDetail")
        self.detail.setWordWrap(True)
        text.addWidget(self.breadcrumb)
        text.addWidget(self.detail)
        layout.addLayout(text, 1)
        self.provider = QLabel("Локальная LLM")
        self.provider.setObjectName("statusPill")
        layout.addWidget(self.provider)
        self.open_active = set_button_kind(
            QPushButton("Активное занятие"),
            "ghost",
        )
        self.open_active.clicked.connect(
            lambda: self.route_requested.emit(AppRoute.LESSON.value)
        )
        layout.addWidget(self.open_active)
        refresh = set_button_kind(QPushButton("↻"), "ghost")
        refresh.setToolTip("Обновить контекст")
        refresh.setAccessibleName("Обновить текущий контекст")
        refresh.clicked.connect(self.refresh_requested)
        layout.addWidget(refresh)

    def set_snapshot(self, snapshot: CockpitSnapshot) -> None:
        route_title = route_definition(snapshot.route).title
        lesson = snapshot.lesson
        if lesson is None:
            self.breadcrumb.setText(route_title)
            detail = "Активное занятие пока не выбрано"
            if snapshot.crm_error or snapshot.lesson_store_error:
                detail = "Часть данных временно недоступна"
            self.detail.setText(detail)
            self.open_active.setEnabled(False)
        else:
            topic = lesson.topic or subject_label(lesson.subject)
            self.breadcrumb.setText(
                f"{lesson.student.full_name}  ›  {route_title}"
            )
            self.detail.setText(
                f"{lesson.lesson_date:%d.%m.%Y} · "
                f"{subject_label(lesson.subject)} · {topic} · "
                f"{_STATUS_TITLES.get(lesson.status, lesson.status.value)}"
            )
            self.open_active.setEnabled(True)
        self.provider.setText(snapshot.provider)
        self.setAccessibleDescription(
            f"{self.breadcrumb.text()}. {self.detail.text()}"
        )


class TeacherCockpitPage(QWidget):
    route_requested = Signal(str)
    quick_requested = Signal()
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("teacherCockpitPage")
        self.setAccessibleName("Сегодня — рабочая панель преподавателя")
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 4, 2, 4)
        root.setSpacing(12)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Сегодня")
        title.setObjectName("pageTitle")
        self.subtitle = QLabel("Оперативная панель преподавателя")
        self.subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        heading.addLayout(title_box, 1)
        refresh = set_button_kind(QPushButton("Обновить"), "ghost")
        refresh.setAccessibleName("Обновить рабочую панель")
        refresh.clicked.connect(self.refresh_requested)
        heading.addWidget(refresh)
        root.addLayout(heading)

        self.hero = QFrame()
        self.hero.setObjectName("cockpitHero")
        hero_layout = QHBoxLayout(self.hero)
        hero_layout.setContentsMargins(20, 17, 20, 17)
        hero_layout.setSpacing(16)
        hero_text = QVBoxLayout()
        hero_text.setSpacing(4)
        self.hero_time = QLabel("Следующее занятие")
        self.hero_time.setObjectName("cockpitHeroTime")
        self.hero_title = QLabel("Расписание свободно")
        self.hero_title.setObjectName("cockpitHeroTitle")
        self.hero_detail = QLabel(
            "Можно подготовить материалы или запланировать занятие"
        )
        self.hero_detail.setObjectName("muted")
        self.hero_detail.setWordWrap(True)
        hero_text.addWidget(self.hero_time)
        hero_text.addWidget(self.hero_title)
        hero_text.addWidget(self.hero_detail)
        hero_layout.addLayout(hero_text, 1)
        self.hero_secondary = set_button_kind(
            QPushButton("Открыть расписание"),
            "ghost",
        )
        self.hero_secondary.clicked.connect(
            lambda: self.route_requested.emit(AppRoute.SCHEDULE.value)
        )
        hero_layout.addWidget(self.hero_secondary)
        self.hero_primary = set_button_kind(
            QPushButton("Быстрый урок"),
            "primary",
        )
        self.hero_primary.clicked.connect(self.quick_requested)
        hero_layout.addWidget(self.hero_primary)
        root.addWidget(self.hero)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(10)
        self.metric_widgets: dict[str, tuple[QFrame, QLabel, QLabel]] = {}
        for column, (key, label) in enumerate(
            (
                ("students", "Активные ученики"),
                ("lessons", "Занятия на неделе"),
                ("revenue", "План недели"),
                ("jobs", "Фоновые задачи"),
            )
        ):
            card = QFrame()
            card.setObjectName("cockpitCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(15, 12, 15, 12)
            value = QLabel("0")
            value.setObjectName("cockpitMetricValue")
            caption = QLabel(label)
            caption.setObjectName("cockpitMetricLabel")
            card_layout.addWidget(value)
            card_layout.addWidget(caption)
            metrics.addWidget(card, 0, column)
            self.metric_widgets[key] = (card, value, caption)
        root.addLayout(metrics)

        self.pipeline = LessonPipelineWidget()
        self.pipeline.route_requested.connect(self.route_requested)
        root.addWidget(self.pipeline)

        lower = QHBoxLayout()
        lower.setSpacing(12)
        attention_card = QFrame()
        attention_card.setObjectName("attentionCard")
        attention_layout = QVBoxLayout(attention_card)
        attention_layout.setContentsMargins(16, 14, 16, 14)
        attention_header = QHBoxLayout()
        attention_title = QLabel("Требует внимания")
        attention_title.setObjectName("tileTitle")
        attention_header.addWidget(attention_title, 1)
        self.attention_count = QLabel("0")
        self.attention_count.setObjectName("statusPill")
        attention_header.addWidget(self.attention_count)
        attention_layout.addLayout(attention_header)
        self.attention_list = QListWidget()
        self.attention_list.setObjectName("attentionList")
        self.attention_list.setAccessibleName(
            "События, требующие внимания"
        )
        self.attention_list.itemActivated.connect(
            self._attention_activated
        )
        attention_layout.addWidget(self.attention_list, 1)
        lower.addWidget(attention_card, 2)

        quick_card = QFrame()
        quick_card.setObjectName("cockpitCard")
        quick_layout = QVBoxLayout(quick_card)
        quick_layout.setContentsMargins(16, 14, 16, 14)
        quick_title = QLabel("Быстрые действия")
        quick_title.setObjectName("tileTitle")
        quick_layout.addWidget(quick_title)
        for title_text, route in (
            ("Проверить транскрипт", AppRoute.TRANSCRIPT),
            ("Открыть материалы", AppRoute.MATERIALS),
            ("Карточки учеников", AppRoute.STUDENTS),
            ("PDF и LaTeX", AppRoute.LATEX),
        ):
            button = set_button_kind(QPushButton(title_text), "ghost")
            button.clicked.connect(
                lambda _checked=False, current=route: self.route_requested.emit(
                    current.value
                )
            )
            quick_layout.addWidget(button)
        quick_layout.addStretch(1)
        lower.addWidget(quick_card, 1)
        root.addLayout(lower, 1)

    def _attention_activated(self, item: QListWidgetItem) -> None:
        route = item.data(Qt.ItemDataRole.UserRole)
        if route:
            self.route_requested.emit(str(route))

    def set_snapshot(self, snapshot: CockpitSnapshot) -> None:
        self.subtitle.setText(format_dashboard_timestamp(snapshot.created_at))
        next_lesson = snapshot.next_lesson
        if next_lesson is None:
            self.hero_time.setText("Следующее занятие")
            self.hero_title.setText("Расписание свободно")
            detail = "Можно подготовить материалы или запланировать занятие"
            if snapshot.crm_error:
                detail = "Расписание временно недоступно — откройте центр внимания"
            self.hero_detail.setText(detail)
            self.hero_primary.setText("Быстрый урок")
        else:
            minutes = snapshot.minutes_to_next
            if minutes is None:
                timing = next_lesson.starts_at.strftime("%H:%M")
            elif minutes < 0:
                timing = "Занятие уже идёт"
            elif minutes == 0:
                timing = "Начинается сейчас"
            elif minutes < 60:
                timing = f"Через {minutes} мин"
            else:
                timing = next_lesson.starts_at.strftime("%d.%m · %H:%M")
            self.hero_time.setText(timing)
            self.hero_title.setText(next_lesson.student_name)
            detail = subject_label(next_lesson.subject)
            if next_lesson.topic:
                detail += f" · {next_lesson.topic}"
            self.hero_detail.setText(detail)
            self.hero_primary.setText("Начать быстрый урок")

        values = {
            "students": str(
                snapshot.stats.active_students or snapshot.active_students
            ),
            "lessons": str(snapshot.stats.lessons_this_week),
            "revenue": (
                f"{snapshot.stats.planned_revenue_cents / 100:,.0f} ₽"
            ),
            "jobs": str(snapshot.background_jobs),
        }
        for key, value in values.items():
            self.metric_widgets[key][1].setText(value)

        self.pipeline.set_stages(snapshot.pipeline)
        selected_key = None
        current_item = self.attention_list.currentItem()
        if current_item is not None:
            selected_key = current_item.data(Qt.ItemDataRole.UserRole + 1)
        self.attention_list.clear()
        row_to_restore = -1
        if not snapshot.attention:
            calm = QListWidgetItem(
                "✓ Всё спокойно\nСрочных действий сейчас нет"
            )
            calm.setFlags(
                calm.flags() & ~Qt.ItemFlag.ItemIsSelectable
            )
            self.attention_list.addItem(calm)
        else:
            icons = {"critical": "●", "warning": "!", "info": "i"}
            for row, attention in enumerate(snapshot.attention):
                item = QListWidgetItem(
                    f"{icons.get(attention.severity, '•')}  "
                    f"{attention.title}\n{attention.detail}"
                )
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    attention.route.value,
                )
                item.setData(
                    Qt.ItemDataRole.UserRole + 1,
                    attention.key,
                )
                item.setToolTip(attention.detail)
                item.setAccessibleDescription(attention.detail)
                self.attention_list.addItem(item)
                if attention.key == selected_key:
                    row_to_restore = row
        if row_to_restore >= 0:
            self.attention_list.setCurrentRow(row_to_restore)
        self.attention_count.setText(str(len(snapshot.attention)))


class TeacherCockpitController(QObject):
    """Installs and coordinates the UX-6 layer around the production window."""

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.navigation: Any | None = None
        self.current_route = AppRoute.TODAY
        self.dashboard = TeacherCockpitPage()
        self.context_bar = GlobalContextBar()
        self.palette = CommandPalette(window)
        self.session: UISessionStore | None = None
        self.last_snapshot = build_cockpit_snapshot(window)
        self.dashboard_index = self.window.tabs.addTab(
            self.dashboard,
            "09  Сегодня",
        )
        if self.dashboard_index != 8:
            raise RuntimeError(
                "Экран «Сегодня» должен сохранять legacy-индексы 0–7"
            )
        central_layout = self.window.centralWidget().layout()
        central_layout.insertWidget(1, self.context_bar)

        self.dashboard.route_requested.connect(self.navigate)
        self.dashboard.quick_requested.connect(
            lambda: self.window._set_mode("quick")
        )
        self.dashboard.refresh_requested.connect(self.refresh)
        self.context_bar.route_requested.connect(self.navigate)
        self.context_bar.refresh_requested.connect(self.refresh)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(30_000)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()

        self.palette_shortcut = QShortcut(
            QKeySequence("Ctrl+K"),
            self.window,
        )
        self.palette_shortcut.setContext(
            Qt.ShortcutContext.ApplicationShortcut
        )
        self.palette_shortcut.activated.connect(self.open_palette)

        self.refresh_shortcut = QShortcut(
            QKeySequence("F5"),
            self.window,
        )
        self.refresh_shortcut.setContext(
            Qt.ShortcutContext.ApplicationShortcut
        )
        self.refresh_shortcut.activated.connect(self.refresh)

        self.route_shortcuts: list[QShortcut] = []
        _install_stylesheet()

    def bind_navigation(self, navigation: Any) -> None:
        self.navigation = navigation
        self.current_route = navigation.current_route()
        navigation.route_changed.connect(self._route_changed)
        navigation.command_palette_requested.connect(self.open_palette)
        self.session = UISessionStore(
            self.window,
            route_provider=navigation.current_route,
        )
        self.session.restore_window()
        materials_splitter = getattr(
            self.window.student_content_page,
            "content_splitter",
            None,
        )
        self.session.register_splitter("materials", materials_splitter)
        students_splitter = self.window.crm_students_page.findChild(
            QSplitter
        )
        self.session.register_splitter("students", students_splitter)
        navigation.set_collapsed(
            self.session.sidebar_collapsed()
        )
        navigation.collapsed_changed.connect(
            self.session.record_sidebar_collapsed
        )
        navigation.route_changed.connect(self.session.record_route)
        self.session.restore_deferred(
            self.window.student_content_page
        )
        self._install_route_shortcuts()
        self.refresh()

    def restore_session(self, default_mode: str) -> None:
        if self.session is None or self.navigation is None:
            self.window._set_mode(default_mode)
            return
        mode = (
            self.session.preferred_mode(default_mode)
            if self.session.initialized
            else default_mode
        )
        if mode == "quick":
            self.window._set_mode("quick")
        else:
            self.window._set_mode("detailed")
            self.navigation.navigate(
                self.session.preferred_route()
            )
        self.session.mark_initialized()

    def _install_route_shortcuts(self) -> None:
        for definition in ROUTE_DEFINITIONS:
            shortcut = QShortcut(
                QKeySequence(definition.shortcut),
                self.window,
            )
            shortcut.setContext(
                Qt.ShortcutContext.ApplicationShortcut
            )
            shortcut.activated.connect(
                lambda route=definition.route: self.navigate(
                    route.value
                )
            )
            self.route_shortcuts.append(shortcut)

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
        name = str(getattr(student, "full_name", ""))
        self.navigate(AppRoute.STUDENTS.value)
        search = getattr(
            self.window.crm_students_page,
            "search",
            None,
        )
        if search is not None:
            search.setText(name)
            search.setFocus(
                Qt.FocusReason.ShortcutFocusReason
            )

    def commands(self) -> list[PaletteCommand]:
        commands: list[PaletteCommand] = []
        for definition in ROUTE_DEFINITIONS:
            commands.append(
                PaletteCommand(
                    f"route:{definition.route.value}",
                    definition.title,
                    f"Открыть раздел · {definition.group.title()}",
                    lambda route=definition.route: self.navigate(
                        route.value
                    ),
                    definition.keywords,
                    definition.shortcut,
                )
            )
        for student in getattr(self.window, "students", ()):
            student_id = str(getattr(student, "id", ""))
            commands.append(
                PaletteCommand(
                    f"student:{student_id}",
                    str(getattr(student, "full_name", student_id)),
                    "Открыть карточку ученика",
                    lambda current=student: self._student_command(
                        current
                    ),
                    (student_id, "ученик", "карточка"),
                )
            )
        next_lesson = self.last_snapshot.next_lesson
        if next_lesson is not None:
            commands.append(
                PaletteCommand(
                    "next-lesson",
                    (
                        "Следующее занятие: "
                        f"{next_lesson.student_name}"
                    ),
                    next_lesson.starts_at.strftime(
                        "%d.%m · %H:%M"
                    ),
                    lambda: self.navigate(
                        AppRoute.SCHEDULE.value
                    ),
                    (
                        "следующее",
                        "расписание",
                        next_lesson.student_name,
                    ),
                )
            )
        commands.extend(
            (
                PaletteCommand(
                    "system:refresh",
                    "Обновить Teacher Cockpit",
                    (
                        "Пересчитать расписание, pipeline "
                        "и центр внимания"
                    ),
                    self.refresh,
                    ("refresh", "обновить", "состояние"),
                    "F5",
                ),
                PaletteCommand(
                    "system:diagnostics",
                    "Собрать диагностический пакет",
                    (
                        "Создать безопасный ZIP без аудио "
                        "и транскриптов"
                    ),
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
                    (
                        "Провайдер, модель и параметры "
                        "повторных запросов"
                    ),
                    self.window._show_normalization_settings,
                    (
                        "ollama",
                        "yandex",
                        "модель",
                        "настройки",
                    ),
                ),
            )
        )
        return commands

    def open_palette(self) -> None:
        self.palette.open_with_commands(self.commands())

    def refresh(self) -> None:
        route = (
            self.navigation.current_route()
            if self.navigation is not None
            else self.current_route
        )
        self.current_route = route
        self.last_snapshot = build_cockpit_snapshot(
            self.window,
            route=route,
        )
        self.dashboard.set_snapshot(self.last_snapshot)
        self.context_bar.set_snapshot(self.last_snapshot)
        if self.navigation is not None:
            counts = Counter(
                item.route
                for item in self.last_snapshot.attention
            )
            self.navigation.set_badges(counts)


def install_teacher_cockpit(
    window: Any,
) -> TeacherCockpitController:
    return TeacherCockpitController(window)


def _install_stylesheet() -> None:
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        return
    if application.property("ux6TeacherCockpitStyle"):
        return
    application.setStyleSheet(
        application.styleSheet() + COCKPIT_STYLESHEET
    )
    application.setProperty(
        "ux6TeacherCockpitStyle",
        True,
    )
