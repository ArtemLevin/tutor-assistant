from __future__ import annotations

import inspect

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tutor_assistant.ui import app as base_app
from tutor_assistant.ui.information_architecture import (
    SidebarNavigation,
    install_information_architecture,
)
from tutor_assistant.ui.transcript_publication_app import MainWindow

_APPLICATION: QApplication | None = None


def _application() -> QApplication:
    global _APPLICATION
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        _APPLICATION = existing
    elif _APPLICATION is None:
        _APPLICATION = QApplication([])
    return _APPLICATION


def test_sidebar_groups_pages_and_keeps_tab_indices() -> None:
    _application()
    tabs = QTabWidget()
    for index in range(8):
        tabs.addTab(QWidget(), f"Page {index}")
    navigation = SidebarNavigation(tabs)

    assert tabs.tabBar().isHidden()
    assert navigation.button_for_page(0).text() == "Подготовка занятия"
    assert navigation.button_for_page(5).text() == "Ученики"
    assert navigation.button_for_page(3).text() == "PDF и LaTeX"

    navigation.button_for_page(6).click()

    assert tabs.currentIndex() == 6
    assert navigation.button_for_page(6).property("active") is True
    assert navigation.button_for_page(0).property("active") is False


def test_sidebar_emits_quick_lesson_request() -> None:
    _application()
    tabs = QTabWidget()
    for index in range(8):
        tabs.addTab(QWidget(), f"Page {index}")
    navigation = SidebarNavigation(tabs)
    requests: list[bool] = []
    navigation.quick_requested.connect(lambda: requests.append(True))

    assert navigation.quick_button is not None
    navigation.quick_button.click()

    assert requests == [True]
    assert tabs.currentIndex() == 0


def test_production_window_builds_publication_policy_directly() -> None:
    source = inspect.getsource(MainWindow)

    assert "install_information_architecture" in source
    assert "Опубликуйте транскрипт" in source
    assert "Опубликовать transcript.txt в main" in source
    assert "findChildren(QLabel)" not in source
    assert "label.text() ==" not in source


class _NavigationWindowHarness:
    def __init__(self) -> None:
        self.quick_page = QWidget()
        quick_layout = QVBoxLayout(self.quick_page)
        self.quick_student = QLineEdit()
        quick_layout.addWidget(self.quick_student)
        self.detailed_mode_button = QPushButton("Рабочее пространство")
        quick_layout.addWidget(self.detailed_mode_button)

        self.tabs = QTabWidget()
        for index in range(8):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.addWidget(QLineEdit(f"Поле страницы {index}"))
            self.tabs.addTab(page, f"Page {index}")

        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.quick_page)
        self.content_stack.addWidget(self.tabs)
        self.content_stack.setCurrentWidget(self.quick_page)
        self.detailed_mode_button.clicked.connect(lambda _checked=False: self._set_mode("detailed"))

    def _set_mode(self, mode: str) -> None:
        target = self.quick_page if mode == "quick" else getattr(self, "navigation_shell", self.tabs)
        self.content_stack.setCurrentWidget(target)


def test_hidden_workspace_tabs_are_restored_and_every_menu_item_opens() -> None:
    application = _application()
    window = _NavigationWindowHarness()
    window.content_stack.resize(1180, 760)
    window.content_stack.show()
    application.processEvents()
    assert window.tabs.isHidden()

    window.navigation_shell = install_information_architecture(window)
    window.detailed_mode_button.click()
    application.processEvents()

    assert window.content_stack.currentWidget() is window.navigation_shell
    assert window.tabs.isVisibleTo(window.navigation_shell)

    for index in range(window.tabs.count()):
        button = window.navigation_shell.button_for_page(index)
        assert button is not None
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        application.processEvents()
        assert window.tabs.currentIndex() == index
        assert window.tabs.currentWidget().isVisibleTo(window.tabs)

    window.content_stack.close()


def test_workspace_and_quick_mode_restore_keyboard_focus() -> None:
    application = _application()
    window = _NavigationWindowHarness()
    window.content_stack.resize(1180, 760)
    window.content_stack.show()
    window.navigation_shell = install_information_architecture(window)

    window.detailed_mode_button.setFocus()
    QTest.keyClick(window.detailed_mode_button, Qt.Key.Key_Space)
    application.processEvents()
    active_button = window.navigation_shell.button_for_page(window.tabs.currentIndex())
    assert active_button is not None
    assert QApplication.focusWidget() is active_button

    assert window.navigation_shell.quick_button is not None
    window.navigation_shell.quick_button.setFocus()
    QTest.keyClick(window.navigation_shell.quick_button, Qt.Key.Key_Return)
    application.processEvents()
    assert window.content_stack.currentWidget() is window.quick_page
    assert QApplication.focusWidget() is window.quick_student

    window.content_stack.close()


def test_base_mode_switch_targets_navigation_shell_semantically() -> None:
    source = inspect.getsource(base_app.MainWindow._set_mode)
    assert "setCurrentWidget" in source
    assert "navigation_shell" in source
