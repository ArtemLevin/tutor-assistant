from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from ..application.workspace import LessonWorkspaceContext, WorkspaceContextSnapshot
from ..crm import CrmStats, ScheduledLesson
from ..domain import JobStatus, Lesson
from .app_routes import AppRoute
from .localization import subject_label


class CockpitCrmStore(Protocol):
    def lessons_for_week(self, week_start: date) -> list[ScheduledLesson]: ...

    def stats(self, week_start: date) -> CrmStats: ...


class CockpitLessonStore(Protocol):
    def list(self, limit: int = 100) -> list[Lesson]: ...


@dataclass(frozen=True, slots=True)
class CockpitDataInputs:
    created_at: datetime
    workspace: WorkspaceContextSnapshot
    scheduled_lessons: tuple[ScheduledLesson, ...]
    stats: CrmStats
    stored_lessons: tuple[Lesson, ...]
    active_students: int
    background_jobs: int
    provider: str
    crm_error: str | None = None
    lesson_store_error: str | None = None


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
    workspace: WorkspaceContextSnapshot
    lesson: LessonWorkspaceContext | None
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


STATUS_TITLES = {
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
_STAGE_DATA = (
    ("prepare", "Подготовка", AppRoute.LESSON),
    ("record", "Запись", AppRoute.LESSON),
    ("transcribe", "Транскрипция", AppRoute.PROCESSING),
    ("review", "Проверка", AppRoute.TRANSCRIPT),
    ("publish", "Публикация", AppRoute.PUBLICATION),
    ("materials", "Материалы", AppRoute.LATEX),
)


def format_dashboard_timestamp(value: datetime) -> str:
    return (
        f"{_RUSSIAN_WEEKDAYS[value.weekday()]}, {value:%d.%m.%Y}"
        f" · обновлено {value:%H:%M}"
    )


def count_running_workers(workers: Iterable[object]) -> int:
    count = 0
    for worker in workers:
        try:
            count += int(bool(worker.isRunning()))
        except (AttributeError, RuntimeError):
            continue
    return count


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _scheduled_context(
    store: CockpitCrmStore | None,
    now: datetime,
) -> tuple[list[ScheduledLesson], CrmStats, str | None]:
    if store is None:
        return [], CrmStats(0, 0, 0), None
    monday = _week_start(now.date())
    try:
        current = list(store.lessons_for_week(monday))
        following = list(store.lessons_for_week(monday + timedelta(days=7)))
        return current + following, store.stats(monday), None
    except Exception as exc:
        logging.exception("Teacher Cockpit: CRM data unavailable")
        return [], CrmStats(0, 0, 0), str(exc) or type(exc).__name__


def _stored_lessons(
    store: CockpitLessonStore | None,
) -> tuple[list[Lesson], str | None]:
    if store is None:
        return [], None
    try:
        try:
            lessons = list(store.list(limit=250))
        except TypeError:
            lessons = list(store.list())
    except Exception as exc:
        logging.exception("Teacher Cockpit: lesson store unavailable")
        return [], str(exc) or type(exc).__name__

    unique: dict[str, Lesson] = {}
    for lesson in lessons:
        if isinstance(lesson, Lesson):
            unique[lesson.lesson_id] = lesson
    return list(unique.values()), None


def collect_cockpit_inputs(
    *,
    workspace: WorkspaceContextSnapshot,
    crm_store: CockpitCrmStore | None,
    lesson_store: CockpitLessonStore | None,
    active_students: int,
    workers: Iterable[object],
    provider_value: str,
    now: datetime | None = None,
) -> CockpitDataInputs:
    current_time = now or datetime.now()
    scheduled, stats, crm_error = _scheduled_context(crm_store, current_time)
    stored, lesson_store_error = _stored_lessons(lesson_store)
    provider = (
        "Yandex AI Studio"
        if provider_value == "yandex_ai_studio"
        else "Локальная LLM"
    )
    return CockpitDataInputs(
        created_at=current_time,
        workspace=workspace,
        scheduled_lessons=tuple(scheduled),
        stats=stats,
        stored_lessons=tuple(stored),
        active_students=max(0, int(active_students)),
        background_jobs=count_running_workers(workers),
        provider=provider,
        crm_error=crm_error,
        lesson_store_error=lesson_store_error,
    )


def _next_scheduled_lesson(
    lessons: tuple[ScheduledLesson, ...],
    now: datetime,
) -> tuple[ScheduledLesson | None, int | None]:
    candidates = [
        item for item in lessons if item.status != "cancelled" and item.ends_at >= now
    ]
    if not candidates:
        return None, None
    selected = min(candidates, key=lambda item: item.starts_at)
    minutes = round((selected.starts_at - now).total_seconds() / 60)
    return selected, minutes


def _pipeline_for_lesson(
    lesson: LessonWorkspaceContext | None,
) -> tuple[PipelineStage, ...]:
    if lesson is None:
        return tuple(
            PipelineStage(key, title, route, "pending", "Занятие пока не выбрано")
            for key, title, route in _STAGE_DATA
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
    elif status in {JobStatus.GENERATED_TEX, JobStatus.COMPILING_PDF, JobStatus.GENERATING}:
        completed.update({"prepare", "record", "transcribe", "review", "publish"})
        active = "materials"
    elif status in {JobStatus.PDF_REVIEW_REQUIRED, JobStatus.COMPILE_FAILED}:
        completed.update({"prepare", "record", "transcribe", "review", "publish"})
        active = "materials"
        attention.add("materials")
    elif status == JobStatus.COMPLETED:
        completed.update(key for key, _title, _route in _STAGE_DATA)
        active = ""
    elif status == JobStatus.FAILED:
        active = "transcribe" if lesson.source_audio_local else "prepare"
        attention.add(active)

    detail = STATUS_TITLES.get(status, status.value)
    result: list[PipelineStage] = []
    for key, title, route in _STAGE_DATA:
        state = "completed" if key in completed else "pending"
        if key == active:
            state = "attention" if key in attention else "active"
        result.append(PipelineStage(key, title, route, state, detail))
    return tuple(result)


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
        route = AppRoute.LATEX if lesson.status == JobStatus.COMPILE_FAILED else AppRoute.PROCESSING
        return AttentionItem(
            f"failed-{lesson.lesson_id}",
            "critical",
            route,
            "Обработка занятия остановлена",
            lesson.error or detail,
        )
    return None


def _attention_items(
    *,
    active_students: int,
    stored_lessons: tuple[Lesson, ...],
    scheduled_lessons: tuple[ScheduledLesson, ...],
    now: datetime,
    background_jobs: int,
    crm_error: str | None,
    lesson_store_error: str | None,
) -> tuple[AttentionItem, ...]:
    items: list[AttentionItem] = []
    if not active_students and not stored_lessons:
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
        identity = scheduled.occurrence_id or scheduled.rule_id
        items.append(
            AttentionItem(
                f"overdue-{identity}-{scheduled.starts_at.isoformat()}",
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

    unique = {item.key: item for item in items}
    severity = {"critical": 0, "warning": 1, "info": 2}
    ordered = sorted(
        unique.values(),
        key=lambda item: (severity.get(item.severity, 3), item.title, item.key),
    )
    return tuple(ordered[:12])


def build_cockpit_snapshot(
    inputs: CockpitDataInputs,
    *,
    route: AppRoute = AppRoute.TODAY,
) -> CockpitSnapshot:
    next_lesson, minutes = _next_scheduled_lesson(
        inputs.scheduled_lessons,
        inputs.created_at,
    )
    focus_lesson = inputs.workspace.focus_lesson
    attention = _attention_items(
        active_students=inputs.active_students,
        stored_lessons=inputs.stored_lessons,
        scheduled_lessons=inputs.scheduled_lessons,
        now=inputs.created_at,
        background_jobs=inputs.background_jobs,
        crm_error=inputs.crm_error,
        lesson_store_error=inputs.lesson_store_error,
    )
    return CockpitSnapshot(
        created_at=inputs.created_at,
        route=route,
        workspace=inputs.workspace,
        lesson=focus_lesson,
        next_lesson=next_lesson,
        minutes_to_next=minutes,
        stats=inputs.stats,
        active_students=inputs.active_students,
        background_jobs=inputs.background_jobs,
        provider=inputs.provider,
        pipeline=_pipeline_for_lesson(focus_lesson),
        attention=attention,
        crm_error=inputs.crm_error,
        lesson_store_error=inputs.lesson_store_error,
    )
