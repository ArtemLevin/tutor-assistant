from __future__ import annotations

import inspect

from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

from tutor_assistant.ui.information_architecture import SidebarNavigation
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


def test_production_window_builds_publication_policy_directly() -> None:
    source = inspect.getsource(MainWindow)

    assert "install_information_architecture" in source
    assert "Опубликуйте транскрипт" in source
    assert "Опубликовать transcript.txt в main" in source
    assert "findChildren(QLabel)" not in source
    assert "label.text() ==" not in source
