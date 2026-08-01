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
    target.write_text(content.rstrip() + "\n\n" + addition.strip() + "\n", encoding="utf-8", newline="\n")


replace_once(
    "src/tutor_assistant/ui/information_architecture.py",
    '''    def _activate_quick(self, source: QPushButton) -> None:\n        self.quick_requested.emit()\n        if source.hasFocus():\n            QTimer.singleShot(0, source.setFocus)\n''',
    '''    def _activate_quick(self, _source: QPushButton) -> None:\n        self.quick_requested.emit()\n''',
)
replace_once(
    "src/tutor_assistant/ui/information_architecture.py",
    '''    def _activate_page(self, index: int, source: QPushButton) -> None:\n        self.tabs.setCurrentIndex(index)\n        if source.hasFocus():\n            QTimer.singleShot(0, self._focus_current_page)\n''',
    '''    def _activate_page(self, index: int, source: QPushButton) -> None:\n        if not 0 <= index < self.tabs.count():\n            return\n        self.tabs.setVisible(True)\n        self.tabs.setCurrentIndex(index)\n        current_page = self.tabs.currentWidget()\n        if current_page is not None:\n            current_page.setVisible(True)\n        if source.hasFocus():\n            QTimer.singleShot(0, self._focus_current_page)\n''',
)
replace_once(
    "src/tutor_assistant/ui/information_architecture.py",
    '''    def ordered_buttons(self) -> tuple[QPushButton, ...]:\n        return tuple(self._button_order)\n\n    def _sync_active(self, current_index: int) -> None:\n''',
    '''    def ordered_buttons(self) -> tuple[QPushButton, ...]:\n        return tuple(self._button_order)\n\n    def focus_current_button(self) -> None:\n        button = self.buttons.get(self.tabs.currentIndex())\n        if button is None or not button.isVisibleTo(self) or not button.isEnabled():\n            return\n        button.setFocus(Qt.FocusReason.TabFocusReason)\n\n    def _sync_active(self, current_index: int) -> None:\n''',
)
replace_once(
    "src/tutor_assistant/ui/information_architecture.py",
    '''def install_information_architecture(window) -> SidebarNavigation:\n    tabs = window.tabs\n    stack = window.content_stack\n    was_detailed = stack.currentWidget() is tabs\n    navigation = SidebarNavigation(tabs)\n    navigation.quick_requested.connect(lambda: window._set_mode("quick"))\n    stack.removeWidget(tabs)\n    stack.insertWidget(1, navigation)\n    if was_detailed:\n        stack.setCurrentWidget(navigation)\n    return navigation\n''',
    '''def install_information_architecture(window) -> SidebarNavigation:\n    tabs = window.tabs\n    stack = window.content_stack\n    was_detailed = stack.currentWidget() is tabs\n\n    # QStackedWidget explicitly hides inactive pages. Remove the tab widget first,\n    # then reparent it into the navigation shell and restore its own visibility.\n    stack.removeWidget(tabs)\n    navigation = SidebarNavigation(tabs)\n    stack.insertWidget(1, navigation)\n    tabs.setVisible(True)\n    tabs.setEnabled(True)\n\n    def open_quick_mode() -> None:\n        window._set_mode("quick")\n        target = getattr(window, "quick_student", None)\n        if isinstance(target, QWidget):\n            QTimer.singleShot(\n                0,\n                lambda: target.setFocus(Qt.FocusReason.TabFocusReason),\n            )\n\n    navigation.quick_requested.connect(open_quick_mode)\n    detailed_button = getattr(window, "detailed_mode_button", None)\n    if isinstance(detailed_button, QPushButton):\n        detailed_button.clicked.connect(\n            lambda _checked=False: QTimer.singleShot(\n                0,\n                navigation.focus_current_button,\n            )\n        )\n\n    if was_detailed:\n        stack.setCurrentWidget(navigation)\n    return navigation\n''',
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    '''        self.content_stack.setCurrentIndex(0 if quick else 1)\n''',
    '''        target = (\n            self.quick_page\n            if quick\n            else getattr(self, "navigation_shell", self.tabs)\n        )\n        target_index = self.content_stack.indexOf(target)\n        if target_index >= 0:\n            self.content_stack.setCurrentWidget(target)\n        else:\n            self.content_stack.setCurrentIndex(0 if quick else 1)\n''',
)

replace_once(
    "tests/test_information_architecture_gui.py",
    '''import inspect\n\nfrom PySide6.QtWidgets import QApplication, QTabWidget, QWidget\n\nfrom tutor_assistant.ui.information_architecture import SidebarNavigation\n''',
    '''import inspect\n\nfrom PySide6.QtCore import Qt\nfrom PySide6.QtTest import QTest\nfrom PySide6.QtWidgets import (\n    QApplication,\n    QLineEdit,\n    QPushButton,\n    QStackedWidget,\n    QTabWidget,\n    QVBoxLayout,\n    QWidget,\n)\n\nfrom tutor_assistant.ui import app as base_app\nfrom tutor_assistant.ui.information_architecture import (\n    SidebarNavigation,\n    install_information_architecture,\n)\n''',
)
append_once(
    "tests/test_information_architecture_gui.py",
    "test_hidden_workspace_tabs_are_restored_and_every_menu_item_opens",
    '''
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
    QTest.keyClick(window.detailed_mode_button, Qt.Key.Key_Return)
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
''',
)

replace_once(
    "pyproject.toml",
    'version = "0.22.0"',
    'version = "0.22.1"',
)
replace_once(
    "src/tutor_assistant/__init__.py",
    '__version__ = "0.22.0"',
    '__version__ = "0.22.1"',
)
replace_once(
    "README.md",
    "Текущая версия: **0.22.0**.",
    "Текущая версия: **0.22.1**.",
)
append_once(
    "README.md",
    "## UX-5: регрессионное UI/UX-ревью",
    '''
## UX-5: регрессионное UI/UX-ревью

- исправлено открытие разделов «Рабочего пространства» после старта в быстром режиме;
- переключение quick/workspace использует семантические виджеты вместо хрупких индексов stack;
- при входе в workspace фокус переходит на активный пункт sidebar;
- при возврате в быстрый урок фокус переходит к выбору ученика;
- GUI-регрессия проверяет мышью все восемь разделов после reparenting скрытого `QTabWidget`.
''',
)
Path("docs/ui-ux-review-2026-08-01.md").write_text(
    '''# UI/UX regression review — 2026-08-01

## Критические дефекты

1. `QTabWidget` оставался скрытым после переноса из неактивной страницы `QStackedWidget` в navigation shell. Sidebar менял индекс, однако пользователь не видел выбранный раздел.
2. Вход в «Рабочее пространство» оставлял клавиатурный фокус на скрытой кнопке быстрого режима.
3. Возврат в «Быстрый урок» оставлял фокус на скрытом пункте sidebar.
4. Переключение режимов опиралось на числовой индекс stack, чувствительный к изменению структуры интерфейса.
5. Автотесты проверяли программный `click()`, но не проверяли видимость страниц после реального reparenting из скрытого состояния.

## Исправления

- `QTabWidget` извлекается из stack до создания navigation shell;
- после reparenting явно восстанавливаются `visible` и `enabled`;
- `_set_mode` выбирает `quick_page` или `navigation_shell` через `setCurrentWidget`;
- переходы между режимами восстанавливают фокус на видимом управляющем элементе;
- добавлен mouse/keyboard regression test всех восьми разделов.
''',
    encoding="utf-8",
    newline="\n",
)
