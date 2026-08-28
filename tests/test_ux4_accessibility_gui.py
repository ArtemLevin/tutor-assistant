from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tutor_assistant.crm import CrmStore, ScheduledLesson, StudentProfile
from tutor_assistant.ui.accessibility import (
    ACCESSIBILITY_STYLESHEET,
    apply_accessibility_to_widget,
    install_accessibility,
)
from tutor_assistant.ui.crm import SchedulePage, StudentsPage
from tutor_assistant.ui.information_architecture import SidebarNavigation


class TestCodec:
    def encrypt(self, value: str | None) -> str | None:
        return value

    def decrypt(self, value: str | None) -> str | None:
        return value


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_focus_styles_cover_primary_interactions(application: QApplication) -> None:
    root = QWidget()
    apply_accessibility_to_widget(root)
    stylesheet = application.styleSheet()
    assert ACCESSIBILITY_STYLESHEET in stylesheet
    assert "QPushButton:focus" in stylesheet
    assert "QLineEdit:focus" in stylesheet
    assert "QCheckBox:focus" in stylesheet
    assert "QTableWidget:focus" in stylesheet


def test_sidebar_supports_arrow_home_end_and_activation(application: QApplication) -> None:
    tabs = QTabWidget()
    for label in ("Первый", "Второй", "Третий", "Четвёртый", "Пятый", "Шестой", "Седьмой", "Восьмой"):
        page = QWidget()
        layout = QVBoxLayout(page)
        editor = QLineEdit()
        editor.setAccessibleName(f"Поле раздела {label}")
        layout.addWidget(editor)
        tabs.addTab(page, label)
    navigation = SidebarNavigation(tabs)
    navigation.show()
    application.processEvents()

    ordered = navigation.ordered_buttons()
    ordered[0].setFocus()
    QTest.keyClick(ordered[0], Qt.Key.Key_Down)
    assert QApplication.focusWidget() is ordered[1]
    QTest.keyClick(ordered[1], Qt.Key.Key_End)
    assert QApplication.focusWidget() is ordered[-1]
    QTest.keyClick(ordered[-1], Qt.Key.Key_Home)
    assert QApplication.focusWidget() is ordered[0]

    page_button = navigation.button_for_page(1)
    assert page_button is not None
    page_button.setFocus()
    QTest.keyClick(page_button, Qt.Key.Key_Return)
    application.processEvents()
    assert tabs.currentIndex() == 1
    assert tabs.currentWidget().focusWidget() is not None
    navigation.close()


def test_main_scenario_has_names_and_predictable_tab_order(application: QApplication) -> None:
    window = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)
    window.setCentralWidget(central)
    window.quick_student = QComboBox()
    window.quick_student.addItem("Ученик")
    window.quick_topic = QLineEdit()
    window.quick_readiness_button = QPushButton("Проверить")
    window.quick_options_button = QPushButton("···")
    window.quick_options_button.setToolTip("Настройки быстрого урока")
    window.quick_start_button = QPushButton("Начать занятие")
    window.detailed_mode_button = QPushButton("Рабочее пространство")
    window.header_more_button = QPushButton("⋯")
    window.header_more_button.setToolTip("Дополнительные действия приложения")
    for widget in (
        window.quick_student,
        window.quick_topic,
        window.quick_readiness_button,
        window.quick_options_button,
        window.quick_start_button,
        window.detailed_mode_button,
        window.header_more_button,
    ):
        layout.addWidget(widget)
    install_accessibility(window)
    window.show()
    application.processEvents()

    assert window.quick_student.accessibleName() == "Ученик быстрого урока"
    assert window.quick_topic.accessibleName() == "Тема быстрого урока"
    assert window.quick_options_button.accessibleName() == "Настройки быстрого урока"
    assert window.header_more_button.accessibleName() == "Дополнительные действия приложения"
    assert window.quick_student.nextInFocusChain() is window.quick_topic
    assert window.quick_topic.nextInFocusChain() is window.quick_readiness_button
    assert window.quick_readiness_button.focusPolicy() == Qt.FocusPolicy.StrongFocus
    window.close()


def test_crm_tab_order_names_and_text_status(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "crm.sqlite3", TestCodec())
    store.save_student(StudentProfile(id="student", full_name="Ученик"), [])
    page = StudentsPage(store)
    apply_accessibility_to_widget(page)
    page.show()
    page.table.selectRow(0)
    application.processEvents()

    assert page.search.accessibleName() == "Поиск"
    assert page.table.accessibleName() == "Список записей"
    assert page.full_name.accessibleName() == "ФИО ученика"
    assert page.search.nextInFocusChain() is page.table
    page.table.setFocus()
    QTest.keyClick(page.table, Qt.Key.Key_Tab)
    application.processEvents()
    assert QApplication.focusWidget() is page.full_name
    page.full_name.setText("Новое имя")
    page.full_name.textEdited.emit("Новое имя")
    assert page.dirty_label.accessibleDescription() == "Есть несохранённые изменения"
    page.close()


def test_schedule_grid_is_keyboard_focusable_and_textual(
    tmp_path: Path,
    application: QApplication,
) -> None:
    store = CrmStore(tmp_path / "schedule.sqlite3", TestCodec())
    store.save_student(StudentProfile(id="student", full_name="Ученик"), [])
    store.save_one_off(
        ScheduledLesson(
            student_id="student",
            student_name="Ученик",
            starts_at=datetime(2026, 8, 3, 16, 30),
            duration_minutes=90,
            subject="mathematics",
            topic="Доступность",
        )
    )
    page = SchedulePage(store)
    page.week_start = date(2026, 8, 3)
    page.refresh()
    apply_accessibility_to_widget(page)
    page.show()
    application.processEvents()

    row = page._row_for_time(16, 30)
    item = page.grid.item(row, 0)
    assert item is not None
    assert item.text() == "Ученик"
    assert "Дважды щёлкните" in item.toolTip()
    assert page.grid.accessibleName() == "Недельное расписание"
    assert page.grid.focusPolicy() != Qt.FocusPolicy.NoFocus
    page.grid.setFocus()
    QTest.keyClick(page.grid, Qt.Key.Key_Tab)
    application.processEvents()
    assert QApplication.focusWidget() is page.open_selected_button
    page.close()
