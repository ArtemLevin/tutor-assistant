from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    content = target.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one marker in {path}, found {count}: {old!r}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8", newline="\n")


def append_once(path: str, marker: str, addition: str) -> None:
    target = Path(path)
    content = target.read_text(encoding="utf-8")
    if marker in content:
        return
    target.write_text(
        content.rstrip() + "\n\n" + addition.strip() + "\n",
        encoding="utf-8",
        newline="\n",
    )


replace_once(
    "src/tutor_assistant/ui/information_architecture.py",
    """    def _activate_quick(self, source: QPushButton) -> None:
        self.quick_requested.emit()
        if source.hasFocus():
            QTimer.singleShot(0, source.setFocus)
""",
    """    def _activate_quick(self, _source: QPushButton) -> None:
        self.quick_requested.emit()
""",
)
replace_once(
    "src/tutor_assistant/ui/information_architecture.py",
    """    def _activate_page(self, index: int, source: QPushButton) -> None:
        self.tabs.setCurrentIndex(index)
        if source.hasFocus():
            QTimer.singleShot(0, self._focus_current_page)
""",
    """    def _activate_page(self, index: int, source: QPushButton) -> None:
        if not 0 <= index < self.tabs.count():
            return
        self.tabs.setVisible(True)
        self.tabs.setCurrentIndex(index)
        current_page = self.tabs.currentWidget()
        if current_page is not None:
            current_page.setVisible(True)
        if source.hasFocus():
            QTimer.singleShot(0, self._focus_current_page)
""",
)
replace_once(
    "src/tutor_assistant/ui/information_architecture.py",
    """    def ordered_buttons(self) -> tuple[QPushButton, ...]:
        return tuple(self._button_order)

    def _sync_active(self, current_index: int) -> None:
""",
    """    def ordered_buttons(self) -> tuple[QPushButton, ...]:
        return tuple(self._button_order)

    def focus_current_button(self) -> None:
        button = self.buttons.get(self.tabs.currentIndex())
        if button is None or not button.isEnabled():
            return
        button.setFocus(Qt.FocusReason.TabFocusReason)

    def _sync_active(self, current_index: int) -> None:
""",
)
replace_once(
    "src/tutor_assistant/ui/information_architecture.py",
    """def install_information_architecture(window) -> SidebarNavigation:
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
""",
    """def install_information_architecture(window) -> SidebarNavigation:
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
""",
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    """        self.content_stack.setCurrentIndex(0 if quick else 1)
""",
    """        target = (
            self.quick_page
            if quick
            else getattr(self, "navigation_shell", self.tabs)
        )
        target_index = self.content_stack.indexOf(target)
        if target_index >= 0:
            self.content_stack.setCurrentWidget(target)
        else:
            self.content_stack.setCurrentIndex(0 if quick else 1)
""",
)

replace_once(
    "tests/test_information_architecture_gui.py",
    """import inspect

from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

from tutor_assistant.ui.information_architecture import SidebarNavigation
""",
    """import inspect

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
""",
)
append_once(
    "tests/test_information_architecture_gui.py",
    "test_hidden_workspace_tabs_are_restored_and_every_menu_item_opens",
    """
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
        self.detailed_mode_button.clicked.connect(
            lambda _checked=False: self._set_mode("detailed")
        )

    def _set_mode(self, mode: str) -> None:
        target = (
            self.quick_page
            if mode == "quick"
            else getattr(self, "navigation_shell", self.tabs)
        )
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
    active_button = window.navigation_shell.button_for_page(
        window.tabs.currentIndex()
    )
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
""",
)
