from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
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

from .app_routes import (
    AppRoute,
    ROUTE_DEFINITIONS,
    page_for_route,
    route_definition,
    route_for_page,
)
from .theme import refresh_style


SIDEBAR_STYLESHEET = """
QFrame#informationArchitectureShell {
    background: transparent;
    border: 0;
}

QFrame#sideNavigation {
    min-width: 72px;
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

QPushButton#sideNavigationButton,
QPushButton#sideNavigationUtility {
    min-height: 38px;
    padding: 0 11px;
    text-align: left;
    color: #526174;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    font-weight: 600;
}

QPushButton#sideNavigationButton:hover,
QPushButton#sideNavigationUtility:hover {
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

QPushButton#sideNavigationButton:focus,
QPushButton#sideNavigationUtility:focus {
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
    route_changed = Signal(str)
    collapsed_changed = Signal(bool)
    command_palette_requested = Signal()

    expanded_width = 272
    collapsed_width = 72

    def __init__(self, tabs: QTabWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tabs = tabs
        self.setObjectName("informationArchitectureShell")
        self.setAccessibleName("Боковая навигация рабочего пространства")
        self.setAccessibleDescription(
            "Используйте Tab, стрелки, Home, End и Enter для выбора раздела"
        )
        self.buttons: dict[int, QPushButton] = {}
        self.route_buttons: dict[AppRoute, QPushButton] = {}
        self.quick_button: QPushButton | None = None
        self._button_order: list[QPushButton] = []
        self._button_labels: dict[AppRoute, str] = {}
        self._badges: dict[AppRoute, int] = {}
        self._group_labels: list[QLabel] = []
        self._collapsed = False
        self._last_announced_route: AppRoute | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sideNavigation")
        self.sidebar.setFixedWidth(self.expanded_width)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(12, 14, 12, 14)
        sidebar_layout.setSpacing(3)

        title_row = QHBoxLayout()
        self.title = QLabel("Рабочее пространство")
        self.title.setObjectName("sideNavigationTitle")
        title_row.addWidget(self.title, 1)
        self.collapse_button = QPushButton("«")
        self.collapse_button.setObjectName("sideNavigationUtility")
        self.collapse_button.setFixedWidth(38)
        self.collapse_button.setAccessibleName("Свернуть боковую навигацию")
        self.collapse_button.clicked.connect(
            lambda: self.set_collapsed(not self._collapsed)
        )
        title_row.addWidget(self.collapse_button)
        sidebar_layout.addLayout(title_row)

        current_group = ""
        for definition in ROUTE_DEFINITIONS:
            if definition.group != current_group:
                current_group = definition.group
                group_label = QLabel(definition.group)
                group_label.setObjectName("sideNavigationGroup")
                sidebar_layout.addWidget(group_label)
                self._group_labels.append(group_label)
            button = QPushButton()
            button.setObjectName("sideNavigationButton")
            button.setAccessibleName(definition.accessible_name)
            button.setAccessibleDescription(
                f"Сочетание клавиш: {definition.shortcut}"
            )
            button.setProperty("active", False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setToolTip(
                f"{definition.title} · {definition.shortcut}"
            )
            self._button_order.append(button)
            self.route_buttons[definition.route] = button
            self._button_labels[definition.route] = definition.title
            if definition.page_index is None:
                self.quick_button = button
            else:
                self.buttons[definition.page_index] = button
                button.setEnabled(definition.page_index < self.tabs.count())
            button.clicked.connect(
                lambda _checked=False, route=definition.route, source=button: (
                    self._activate_route(route, source)
                )
            )
            sidebar_layout.addWidget(button)

        sidebar_layout.addStretch(1)
        self.command_button = QPushButton("⌘  Команды")
        self.command_button.setObjectName("sideNavigationUtility")
        self.command_button.setAccessibleName("Открыть командную палитру")
        self.command_button.setToolTip("Командная палитра · Ctrl+K")
        self.command_button.clicked.connect(self.command_palette_requested)
        sidebar_layout.addWidget(self.command_button)

        root.addWidget(self.sidebar)
        root.addWidget(tabs, 1)

        self.tabs.tabBar().setVisible(False)
        self.tabs.currentChanged.connect(self._tab_changed)
        self._sync_active(self.tabs.currentIndex())
        self._refresh_button_texts()
        _install_stylesheet()

    def _activate_route(self, route: AppRoute, source: QPushButton) -> None:
        if route == AppRoute.QUICK_LESSON:
            self.quick_requested.emit()
            return
        if not self.navigate(route):
            return
        if source.hasFocus():
            QTimer.singleShot(0, self._focus_current_page)

    def navigate(self, route: AppRoute | str) -> bool:
        parsed = AppRoute(route)
        if parsed == AppRoute.QUICK_LESSON:
            self.quick_requested.emit()
            return True
        page_index = page_for_route(parsed)
        if page_index is None or not 0 <= page_index < self.tabs.count():
            return False
        self.tabs.setVisible(True)
        self.tabs.setEnabled(True)
        changed = self.tabs.currentIndex() != page_index
        self.tabs.setCurrentIndex(page_index)
        page = self.tabs.currentWidget()
        if page is not None:
            page.setVisible(True)
        self._sync_active(page_index)
        if not changed:
            self._announce_route(parsed)
        return True

    def current_route(self) -> AppRoute:
        return route_for_page(self.tabs.currentIndex()) or AppRoute.TODAY

    def _tab_changed(self, current_index: int) -> None:
        self._sync_active(current_index)
        route = route_for_page(current_index)
        if route is not None:
            self._announce_route(route)

    def _announce_route(self, route: AppRoute) -> None:
        if route == self._last_announced_route:
            return
        self._last_announced_route = route
        self.route_changed.emit(route.value)

    def _focus_current_page(self) -> None:
        page = self.tabs.currentWidget()
        if page is None:
            return
        for candidate in page.findChildren(QWidget):
            if (
                candidate.isVisibleTo(page)
                and candidate.isEnabled()
                and candidate.focusPolicy() != Qt.FocusPolicy.NoFocus
            ):
                candidate.setFocus(Qt.FocusReason.TabFocusReason)
                return
        page.setFocus(Qt.FocusReason.TabFocusReason)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        focused = QApplication.focusWidget()
        if focused not in self._button_order:
            super().keyPressEvent(event)
            return
        current = self._button_order.index(focused)
        enabled_buttons = [button for button in self._button_order if button.isEnabled()]
        if not enabled_buttons:
            super().keyPressEvent(event)
            return
        enabled_current = enabled_buttons.index(focused) if focused in enabled_buttons else 0
        key = event.key()
        if key in {Qt.Key.Key_Down, Qt.Key.Key_Right}:
            target = enabled_buttons[(enabled_current + 1) % len(enabled_buttons)]
        elif key in {Qt.Key.Key_Up, Qt.Key.Key_Left}:
            target = enabled_buttons[(enabled_current - 1) % len(enabled_buttons)]
        elif key == Qt.Key.Key_Home:
            target = enabled_buttons[0]
        elif key == Qt.Key.Key_End:
            target = enabled_buttons[-1]
        elif key in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            focused.click()
            event.accept()
            return
        else:
            super().keyPressEvent(event)
            return
        del current
        target.setFocus(Qt.FocusReason.TabFocusReason)
        event.accept()

    def ordered_buttons(self) -> tuple[QPushButton, ...]:
        return tuple(self._button_order)

    def focus_current_button(self) -> None:
        button = self.route_buttons.get(self.current_route())
        if button is None or not button.isEnabled():
            return
        button.setFocus(Qt.FocusReason.TabFocusReason)

    def _sync_active(self, current_index: int) -> None:
        active_route = route_for_page(current_index)
        for route, button in self.route_buttons.items():
            button.setProperty("active", route == active_route)
            refresh_style(button)

    def button_for_page(self, page_index: int) -> QPushButton | None:
        return self.buttons.get(page_index)

    def button_for_route(self, route: AppRoute | str) -> QPushButton | None:
        return self.route_buttons.get(AppRoute(route))

    def set_badges(self, counts: Mapping[AppRoute | str, int]) -> None:
        self._badges = {
            AppRoute(route): max(0, int(count))
            for route, count in counts.items()
            if int(count) > 0
        }
        self._refresh_button_texts()

    def set_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        if self._collapsed == collapsed:
            self._refresh_button_texts()
            return
        self._collapsed = collapsed
        self.sidebar.setFixedWidth(
            self.collapsed_width if collapsed else self.expanded_width
        )
        self.title.setVisible(not collapsed)
        for label in self._group_labels:
            label.setVisible(not collapsed)
        self.collapse_button.setText("»" if collapsed else "«")
        self.collapse_button.setAccessibleName(
            "Развернуть боковую навигацию"
            if collapsed
            else "Свернуть боковую навигацию"
        )
        self.command_button.setText("⌘" if collapsed else "⌘  Команды")
        self._refresh_button_texts()
        self.collapsed_changed.emit(collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _refresh_button_texts(self) -> None:
        for route, button in self.route_buttons.items():
            definition = route_definition(route)
            count = self._badges.get(route, 0)
            badge = f" {count}" if count and self._collapsed else f"  ·  {count}" if count else ""
            if self._collapsed:
                button.setText(f"{definition.icon}{badge}")
                button.setStyleSheet("text-align: center;")
            else:
                button.setText(f"{definition.icon}  {definition.title}{badge}")
                button.setStyleSheet("")
            button.setAccessibleDescription(
                f"{definition.title}. Событий, требующих внимания: {count}. "
                f"Сочетание клавиш: {definition.shortcut}"
            )


def install_information_architecture(window) -> SidebarNavigation:
    tabs = window.tabs
    stack = window.content_stack
    was_detailed = stack.currentWidget() is tabs

    # QStackedWidget hides inactive pages explicitly. Detach the tabs first,
    # then reparent them into the navigation shell and restore visibility.
    stack.removeWidget(tabs)
    navigation = SidebarNavigation(tabs)
    stack.insertWidget(1, navigation)
    tabs.setVisible(True)
    tabs.setEnabled(True)

    def open_quick_mode() -> None:
        window._set_mode("quick")
        target = getattr(window, "quick_student", None)
        if isinstance(target, QWidget):
            QTimer.singleShot(
                0,
                lambda: target.setFocus(Qt.FocusReason.TabFocusReason),
            )

    navigation.quick_requested.connect(open_quick_mode)
    detailed_button = getattr(window, "detailed_mode_button", None)
    if isinstance(detailed_button, QPushButton):
        detailed_button.clicked.connect(
            lambda _checked=False: QTimer.singleShot(
                0,
                navigation.focus_current_button,
            )
        )

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
