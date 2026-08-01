from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .app_routes import AppRoute, route_definition
from .localization import subject_label
from .teacher_cockpit_data import (
    STATUS_TITLES,
    CockpitSnapshot,
    PipelineStage,
    format_dashboard_timestamp,
)
from .theme import refresh_style, set_button_kind

COCKPIT_STYLESHEET = """
QFrame#globalContextBar, QFrame#cockpitHero, QFrame#cockpitCard,
QFrame#pipelineCard, QFrame#attentionCard {
    background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 14px;
}
QFrame#globalContextBar { min-height: 50px; }
QLabel#contextBreadcrumb { color: #344054; font-size: 13px; font-weight: 700; }
QLabel#contextDetail { color: #667085; font-size: 12px; }
QLabel#cockpitHeroTitle { color: #101828; font-size: 23px; font-weight: 750; }
QLabel#cockpitHeroTime { color: #275AA6; font-size: 15px; font-weight: 700; }
QLabel#cockpitMetricValue { color: #101828; font-size: 22px; font-weight: 750; }
QLabel#cockpitMetricLabel { color: #667085; font-size: 11px; }
QPushButton#pipelineStage {
    min-height: 48px; padding: 7px 10px; text-align: left; border-radius: 10px;
    border: 1px solid #D8E0EA; background: #F8FAFC; color: #526174;
    font-weight: 650;
}
QPushButton#pipelineStage[state="completed"] {
    background: #EAF8F1; border-color: #B8E4CF; color: #236B4A;
}
QPushButton#pipelineStage[state="active"] {
    background: #EAF2FF; border-color: #BFD5F6; color: #275AA6;
}
QPushButton#pipelineStage[state="attention"] {
    background: #FFF4D6; border-color: #E9CC7A; color: #845800;
}
QPushButton#pipelineStage:focus { border: 2px solid #4D7FD6; }
QListWidget#attentionList { border: 0; background: transparent; outline: 0; }
"""


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

        icons = {"completed": "✓", "active": "●", "attention": "!", "pending": "○"}
        labels = {
            "completed": "завершено",
            "active": "выполняется",
            "attention": "требует внимания",
            "pending": "ожидает",
        }
        for stage in stages:
            button = self.buttons.get(stage.key) or self._create_button(stage.key)
            button.setText(f"{icons.get(stage.state, '○')}  {stage.title}")
            button.setProperty("state", stage.state)
            button.setProperty("route", stage.route.value)
            button.setToolTip(stage.detail)
            button.setAccessibleName(f"{stage.title}: {labels.get(stage.state, stage.state)}")
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
        self.open_active = set_button_kind(QPushButton("Активное занятие"), "ghost")
        self.open_active.clicked.connect(lambda: self.route_requested.emit(AppRoute.LESSON.value))
        layout.addWidget(self.open_active)
        refresh = set_button_kind(QPushButton("↻"), "ghost")
        refresh.setToolTip("Обновить контекст")
        refresh.setAccessibleName("Обновить текущий контекст")
        refresh.clicked.connect(self.refresh_requested)
        layout.addWidget(refresh)

    def set_snapshot(self, snapshot: CockpitSnapshot) -> None:
        route_title = route_definition(snapshot.route).title
        if snapshot.lesson is None:
            self.breadcrumb.setText(route_title)
            detail = "Активное занятие пока не выбрано"
            if snapshot.crm_error or snapshot.lesson_store_error:
                detail = "Часть данных временно недоступна"
            self.detail.setText(detail)
            self.open_active.setEnabled(False)
        else:
            lesson = snapshot.lesson
            topic = lesson.topic or subject_label(lesson.subject)
            self.breadcrumb.setText(f"{lesson.student.full_name}  ›  {route_title}")
            self.detail.setText(
                f"{lesson.lesson_date:%d.%m.%Y} · {subject_label(lesson.subject)} · "
                f"{topic} · {STATUS_TITLES.get(lesson.status, lesson.status.value)}"
            )
            self.open_active.setEnabled(True)
        self.provider.setText(snapshot.provider)
        self.setAccessibleDescription(f"{self.breadcrumb.text()}. {self.detail.text()}")


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
        self.hero_detail = QLabel("Можно подготовить материалы или запланировать занятие")
        self.hero_detail.setObjectName("muted")
        self.hero_detail.setWordWrap(True)
        hero_text.addWidget(self.hero_time)
        hero_text.addWidget(self.hero_title)
        hero_text.addWidget(self.hero_detail)
        hero_layout.addLayout(hero_text, 1)
        self.hero_secondary = set_button_kind(QPushButton("Открыть расписание"), "ghost")
        self.hero_secondary.clicked.connect(
            lambda: self.route_requested.emit(AppRoute.SCHEDULE.value)
        )
        hero_layout.addWidget(self.hero_secondary)
        self.hero_primary = set_button_kind(QPushButton("Быстрый урок"), "primary")
        self.hero_primary.clicked.connect(self.quick_requested)
        hero_layout.addWidget(self.hero_primary)
        root.addWidget(self.hero)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(10)
        self.metric_widgets: dict[str, tuple[QFrame, QLabel, QLabel]] = {}
        metric_labels = (
            ("students", "Активные ученики"),
            ("lessons", "Занятия на неделе"),
            ("revenue", "План недели"),
            ("jobs", "Фоновые задачи"),
        )
        for column, (key, label) in enumerate(metric_labels):
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
        self.attention_list.setAccessibleName("События, требующие внимания")
        self.attention_list.itemActivated.connect(self._attention_activated)
        attention_layout.addWidget(self.attention_list, 1)
        lower.addWidget(attention_card, 2)

        quick_card = QFrame()
        quick_card.setObjectName("cockpitCard")
        quick_layout = QVBoxLayout(quick_card)
        quick_layout.setContentsMargins(16, 14, 16, 14)
        quick_title = QLabel("Быстрые действия")
        quick_title.setObjectName("tileTitle")
        quick_layout.addWidget(quick_title)
        quick_actions = (
            ("Проверить транскрипт", AppRoute.TRANSCRIPT),
            ("Открыть материалы", AppRoute.MATERIALS),
            ("Карточки учеников", AppRoute.STUDENTS),
            ("PDF и LaTeX", AppRoute.LATEX),
        )
        for title_text, route in quick_actions:
            button = set_button_kind(QPushButton(title_text), "ghost")
            button.clicked.connect(
                lambda _checked=False, current=route: self.route_requested.emit(current.value)
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
        focused_pipeline_key = next(
            (
                key
                for key, button in self.pipeline.buttons.items()
                if QApplication.focusWidget() is button
            ),
            None,
        )
        self.subtitle.setText(format_dashboard_timestamp(snapshot.created_at))
        if snapshot.next_lesson is None:
            self.hero_time.setText("Следующее занятие")
            self.hero_title.setText("Расписание свободно")
            detail = "Можно подготовить материалы или запланировать занятие"
            if snapshot.crm_error:
                detail = "Расписание временно недоступно — откройте центр внимания"
            self.hero_detail.setText(detail)
            self.hero_primary.setText("Быстрый урок")
        else:
            lesson = snapshot.next_lesson
            minutes = snapshot.minutes_to_next
            if minutes is None:
                timing = lesson.starts_at.strftime("%H:%M")
            elif minutes < 0:
                timing = "Занятие уже идёт"
            elif minutes == 0:
                timing = "Начинается сейчас"
            elif minutes < 60:
                timing = f"Через {minutes} мин"
            else:
                timing = lesson.starts_at.strftime("%d.%m · %H:%M")
            self.hero_time.setText(timing)
            self.hero_title.setText(lesson.student_name)
            detail = subject_label(lesson.subject)
            if lesson.topic:
                detail += f" · {lesson.topic}"
            self.hero_detail.setText(detail)
            self.hero_primary.setText("Начать быстрый урок")

        values = {
            "students": str(snapshot.stats.active_students or snapshot.active_students),
            "lessons": str(snapshot.stats.lessons_this_week),
            "revenue": f"{snapshot.stats.planned_revenue_cents / 100:,.0f} ₽",
            "jobs": str(snapshot.background_jobs),
        }
        for key, value in values.items():
            self.metric_widgets[key][1].setText(value)

        self.pipeline.set_stages(snapshot.pipeline)
        selected_key = None
        current = self.attention_list.currentItem()
        if current is not None:
            selected_key = current.data(Qt.ItemDataRole.UserRole + 1)
        self.attention_list.clear()
        restore_row = -1
        if not snapshot.attention:
            calm = QListWidgetItem("✓ Всё спокойно\nСрочных действий сейчас нет")
            calm.setFlags(calm.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.attention_list.addItem(calm)
        else:
            icons = {"critical": "●", "warning": "!", "info": "i"}
            for row, attention in enumerate(snapshot.attention):
                item = QListWidgetItem(
                    f"{icons.get(attention.severity, '•')}  "
                    f"{attention.title}\n{attention.detail}"
                )
                item.setData(Qt.ItemDataRole.UserRole, attention.route.value)
                item.setData(Qt.ItemDataRole.UserRole + 1, attention.key)
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, attention.detail)
                item.setToolTip(attention.detail)
                self.attention_list.addItem(item)
                if attention.key == selected_key:
                    restore_row = row
        if restore_row >= 0:
            self.attention_list.setCurrentRow(restore_row)
        self.attention_count.setText(str(len(snapshot.attention)))
        if focused_pipeline_key in self.pipeline.buttons:
            self.pipeline.buttons[focused_pipeline_key].setFocus(
                Qt.FocusReason.OtherFocusReason
            )
