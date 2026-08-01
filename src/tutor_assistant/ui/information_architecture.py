from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .theme import refresh_style


@dataclass(frozen=True, slots=True)
class NavigationEntry:
    group: str
    label: str
    page_index: int | None
    accessible_name: str


NAVIGATION_ENTRIES = (
    NavigationEntry("РАБОТА", "Быстрый урок", None, "Открыть быстрый запуск занятия"),
    NavigationEntry("РАБОТА", "Подготовка занятия", 0, "Открыть подготовку и запись занятия"),
    NavigationEntry("РАБОТА", "Транскрипт", 1, "Открыть проверку транскрипта"),
    NavigationEntry("РАБОТА", "Публикация", 2, "Открыть публикацию транскрипта"),
    NavigationEntry("РАБОТА", "Фоновая обработка", 4, "Открыть очередь фоновой обработки"),
    NavigationEntry("УЧЕНИКИ", "Ученики", 5, "Открыть карточки учеников"),
    NavigationEntry("УЧЕНИКИ", "Расписание", 6, "Открыть расписание"),
    NavigationEntry("УЧЕНИКИ", "Материалы", 7, "Открыть архив материалов"),
    NavigationEntry("ИНСТРУМЕНТЫ", "PDF и LaTeX", 3, "Открыть инструменты PDF и LaTeX"),
)


SIDEBAR_STYLESHEET = """
QFrame#informationArchitectureShell {
    background: transparent;
    border: 0;
}

QFrame#sideNavigation {
    min-width: 214px;
    max-width: 214px;
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 16px;
}

QLabel#sideNavigationTitle {
    color: #111827;
    font-size: 16px;
    font-weight: 700;
}

QLabel#sideNavigationGroup {
    color: #7A8798;
    font-size: 10px;
    font-weight: 750;
    letter-spacing: 1px;
    padding: 10px 8px 3px 8px;
}

QPushButton#sideNavigationButton {
    min-height: 38px;
    padding: 0 11px;
    text-align: left;
    color: #526174;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    font-weight: 600;
}

QPushButton#sideNavigationButton:hover {
    color: #344054;
    background: #F1F4F8;
    border-color: #E1E6ED;
}

QPushButton#sideNavigationButton[active="true"] {
    color: #275AA6;
    background: #EAF2FF;
    border-color: #CFE0FA;
    font-weight: 700;
}

QPushButton#sideNavigationButton:focus {
    border: 2px solid #4D7FD6;
}

QPushButton#headerMoreButton {
    min-width: 40px;
    max-width: 40px;
    padding: 0;
    font-size: 18px;
    font-weight: 700;
}
"""


class SidebarNavigation(QFrame):
    quick_requested = Signal()

    def __init__(self, tabs: QTabWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tabs = tabs
        self.setObjectName("informationArchitectureShell")
        self.buttons: dict[int, QPushButton] = {}
        self.quick_button: QPushButton | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        sidebar = QFrame()
        sidebar.setObjectName("sideNavigation")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 14, 12, 14)
        sidebar_layout.setSpacing(3)

        title = QLabel("Рабочее пространство")
        title.setObjectName("sideNavigationTitle")
        sidebar_layout.addWidget(title)

        current_group = ""
        for entry in NAVIGATION_ENTRIES:
            if entry.group != current_group:
                current_group = entry.group
                group_label = QLabel(entry.group)
                group_label.setObjectName("sideNavigationGroup")
                sidebar_layout.addWidget(group_label)
            button = QPushButton(entry.label)
            button.setObjectName("sideNavigationButton")
            button.setAccessibleName(entry.accessible_name)
            button.setProperty("active", False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if entry.page_index is None:
                self.quick_button = button
                button.clicked.connect(self.quick_requested.emit)
            else:
                self.buttons[entry.page_index] = button
                button.clicked.connect(
                    lambda _checked=False, index=entry.page_index: self.tabs.setCurrentIndex(index)
                )
            sidebar_layout.addWidget(button)

        sidebar_layout.addStretch(1)
        root.addWidget(sidebar)
        root.addWidget(tabs, 1)

        self.tabs.tabBar().setVisible(False)
        self.tabs.currentChanged.connect(self._sync_active)
        self._sync_active(self.tabs.currentIndex())
        _install_stylesheet()

    def _sync_active(self, current_index: int) -> None:
        for index, button in self.buttons.items():
            button.setProperty("active", index == current_index)
            refresh_style(button)

    def button_for_page(self, page_index: int) -> QPushButton | None:
        return self.buttons.get(page_index)


def install_information_architecture(window) -> SidebarNavigation:
    tabs = window.tabs
    stack = window.content_stack
    was_detailed = stack.currentWidget() is tabs
    navigation = SidebarNavigation(tabs)
    navigation.quick_requested.connect(lambda: window._set_mode("quick"))
    stack.removeWidget(tabs)
    stack.insertWidget(1, navigation)
    if was_detailed:
        stack.setCurrentWidget(navigation)
    return navigation


def _install_stylesheet() -> None:
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        return
    if application.property("ux1InformationArchitectureStyle"):
        return
    application.setStyleSheet(application.styleSheet() + SIDEBAR_STYLESHEET)
    application.setProperty("ux1InformationArchitectureStyle", True)
