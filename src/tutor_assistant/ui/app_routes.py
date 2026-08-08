from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AppRoute(StrEnum):
    TODAY = "today"
    QUICK_LESSON = "quick_lesson"
    LESSON = "lesson"
    TRANSCRIPT = "transcript"
    PUBLICATION = "publication"
    PROCESSING = "processing"
    STUDENTS = "students"
    SCHEDULE = "schedule"
    JOURNAL = "journal"
    MATERIALS = "materials"
    LATEX = "latex"


@dataclass(frozen=True, slots=True)
class RouteDefinition:
    route: AppRoute
    group: str
    title: str
    icon: str
    accessible_name: str
    shortcut: str
    page_index: int | None
    keywords: tuple[str, ...] = ()


ROUTE_DEFINITIONS: tuple[RouteDefinition, ...] = (
    RouteDefinition(
        AppRoute.TODAY,
        "ОБЗОР",
        "Сегодня",
        "⌂",
        "Открыть обзор сегодняшнего дня",
        "Ctrl+0",
        8,
        ("главная", "обзор", "день", "cockpit"),
    ),
    RouteDefinition(
        AppRoute.QUICK_LESSON,
        "РАБОТА",
        "Быстрый урок",
        "⚡",
        "Открыть быстрый запуск занятия",
        "Ctrl+Shift+Q",
        None,
        ("запись", "старт", "урок"),
    ),
    RouteDefinition(
        AppRoute.LESSON,
        "РАБОТА",
        "Подготовка занятия",
        "●",
        "Открыть подготовку и запись занятия",
        "Ctrl+1",
        0,
        ("микрофон", "аудио", "запись"),
    ),
    RouteDefinition(
        AppRoute.TRANSCRIPT,
        "РАБОТА",
        "Транскрипт",
        "T",
        "Открыть проверку транскрипта",
        "Ctrl+2",
        1,
        ("текст", "распознавание", "llm", "фильтрация"),
    ),
    RouteDefinition(
        AppRoute.PUBLICATION,
        "РАБОТА",
        "Публикация",
        "↑",
        "Открыть публикацию транскрипта",
        "Ctrl+3",
        2,
        ("github", "main", "опубликовать"),
    ),
    RouteDefinition(
        AppRoute.PROCESSING,
        "РАБОТА",
        "Фоновая обработка",
        "↻",
        "Открыть очередь фоновой обработки",
        "Ctrl+5",
        4,
        ("задачи", "очередь", "прогресс"),
    ),
    RouteDefinition(
        AppRoute.STUDENTS,
        "УЧЕНИКИ",
        "Ученики",
        "♙",
        "Открыть карточки учеников",
        "Ctrl+6",
        5,
        ("crm", "карточка", "контакты"),
    ),
    RouteDefinition(
        AppRoute.SCHEDULE,
        "УЧЕНИКИ",
        "Расписание",
        "▦",
        "Открыть расписание",
        "Ctrl+7",
        6,
        ("календарь", "занятия", "неделя"),
    ),
    RouteDefinition(
        AppRoute.JOURNAL,
        "УЧЕНИКИ",
        "Журнал занятий",
        "≡",
        "Открыть журнал занятий",
        "Ctrl+9",
        9,
        ("история", "поиск", "фильтр", "оплата", "домашняя работа", "дз"),
    ),
    RouteDefinition(
        AppRoute.MATERIALS,
        "УЧЕНИКИ",
        "Материалы",
        "▤",
        "Открыть архив материалов",
        "Ctrl+8",
        7,
        ("архив", "файлы", "занятия", "поиск"),
    ),
    RouteDefinition(
        AppRoute.LATEX,
        "ИНСТРУМЕНТЫ",
        "PDF и LaTeX",
        "Σ",
        "Открыть инструменты PDF и LaTeX",
        "Ctrl+4",
        3,
        ("tex", "pdf", "компиляция"),
    ),
)

ROUTE_BY_ID = {definition.route: definition for definition in ROUTE_DEFINITIONS}
ROUTE_BY_PAGE_INDEX = {
    definition.page_index: definition.route
    for definition in ROUTE_DEFINITIONS
    if definition.page_index is not None
}


def route_definition(route: AppRoute | str) -> RouteDefinition:
    return ROUTE_BY_ID[AppRoute(route)]


def route_for_page(page_index: int) -> AppRoute | None:
    return ROUTE_BY_PAGE_INDEX.get(page_index)


def page_for_route(route: AppRoute | str) -> int | None:
    return route_definition(route).page_index


def parse_route(value: object, default: AppRoute = AppRoute.TODAY) -> AppRoute:
    try:
        return AppRoute(str(value))
    except (TypeError, ValueError):
        return default
