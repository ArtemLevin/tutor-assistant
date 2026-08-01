from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    content = target.read_text(encoding="utf-8")
    if content.count(old) != 1:
        raise RuntimeError(f"Expected one marker in {path}: {old[:80]!r}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8", newline="\n")


ACCESSIBILITY = r'''from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QTableWidget,
    QWidget,
)


ACCESSIBILITY_STYLESHEET = """
QPushButton:focus, QToolButton:focus {
    border: 2px solid #215DB0;
}

QPushButton[kind="primary"]:focus {
    border: 2px solid #102F62;
}

QPushButton[kind="danger"]:focus {
    border: 2px solid #8F2430;
}

QPushButton[kind="ghost"]:focus, QPushButton[kind="link"]:focus {
    color: #173F7A;
    background: #EAF2FF;
    border: 2px solid #215DB0;
}

QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus,
QDateTimeEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus, QTableWidget:focus,
QListWidget:focus, QTreeWidget:focus {
    border: 2px solid #215DB0;
}

QCheckBox:focus, QRadioButton:focus {
    color: #173F7A;
    background: #EAF2FF;
    border: 1px solid #4D7FD6;
    border-radius: 6px;
    padding: 4px;
}

QTabBar::tab:focus {
    color: #173F7A;
    background: #EAF2FF;
    border: 2px solid #215DB0;
}

QMenu::item:selected {
    color: #173F7A;
    background: #EAF2FF;
}
"""


_WIDGET_NAMES: dict[str, tuple[str, str]] = {
    "quick_profile": ("Профиль быстрого запуска", "Набор сохранённых параметров быстрого урока"),
    "quick_student": ("Ученик быстрого урока", "Выберите ученика для новой записи"),
    "quick_subject": ("Предмет быстрого урока", "Предмет определяет шаблоны материалов"),
    "quick_topic": ("Тема быстрого урока", "Введите тему занятия"),
    "quick_readiness_button": ("Открыть подробную проверку готовности", "Показывает состояние данных и аудиоустройств"),
    "quick_options_button": ("Настройки быстрого урока", "Открыть выбор профиля и предмета"),
    "quick_start_button": ("Основное действие быстрого урока", "Начать, отменить запуск или завершить занятие"),
    "detailed_mode_button": ("Открыть рабочее пространство", "Перейти к расширенным разделам приложения"),
    "header_more_button": ("Дополнительные действия приложения", "Диагностика, журнал и настройки"),
    "student": ("Ученик занятия", "Выберите ученика"),
    "subject": ("Предмет занятия", "Выберите предмет"),
    "topic": ("Тема занятия", "Введите тему урока"),
    "lesson_date": ("Дата занятия", "Выберите дату урока"),
    "mic": ("Микрофон преподавателя", "Выберите устройство записи голоса преподавателя"),
    "loopback": ("Системный звук ученика", "Выберите Windows WASAPI Loopback"),
    "audio_output_format": ("Итоговый формат аудио", "Выберите формат читаемого файла после записи"),
    "test_devices_button": ("Проверить аудиоустройства", "Проверить микрофон и системный звук"),
    "play_mic_test_button": ("Прослушать тест микрофона", "Воспроизвести тестовую запись преподавателя"),
    "play_system_test_button": ("Прослушать тест системного звука", "Воспроизвести тестовую запись ученика"),
    "start_button": ("Начать запись занятия", "Запустить запись микрофона и системного звука"),
    "stop_button": ("Завершить запись занятия", "Остановить и сохранить запись"),
    "audio_path": ("Путь к аудиофайлу", "Локальный путь к записанному или выбранному аудио"),
    "transcribe_button": ("Запустить локальную транскрибацию", "Поставить аудио в очередь Whisper"),
    "segment_table": ("Сегменты транскрипта", "Таблица реплик с временными метками"),
    "play_segment_button": ("Воспроизвести выбранный сегмент", "Проиграть аудио выбранной реплики"),
    "playback_speed": ("Скорость воспроизведения", "Выберите скорость аудио"),
    "transcript": ("Сводный транскрипт", "Проверьте и отредактируйте итоговый текст"),
    "approve": ("Подтвердить транскрипт", "Сохранить проверенный текст и перейти к публикации"),
    "publish_button": ("Опубликовать транскрипт", "Записать подтверждённый transcript.txt в main"),
    "open_pr_button": ("Открыть pull request", "Открыть опубликованный pull request"),
    "search": ("Поиск", "Фильтрация текущего списка"),
    "table": ("Список записей", "Используйте стрелки для выбора строки"),
    "full_name": ("ФИО ученика", "Введите полное имя ученика"),
    "grade": ("Класс ученика", "Укажите школьный класс"),
    "school": ("Школа ученика", "Введите название школы"),
    "exam": ("Экзамен ученика", "Выберите или введите экзамен"),
    "goal": ("Учебная цель", "Введите цель занятий"),
    "target_score": ("Целевой балл", "Укажите желаемый результат"),
    "subjects": ("Предметы ученика", "Перечислите учебные предметы"),
    "rate": ("Ставка занятия", "Укажите стоимость занятия"),
    "active": ("Активный ученик", "Включает ученика в рабочие списки"),
    "technical_toggle": ("Технические параметры ученика", "Показать или скрыть служебные поля"),
    "student_id": ("Внутренний ID ученика", "Служебный идентификатор карточки"),
    "timezone": ("Часовой пояс ученика", "Часовой пояс для расписания"),
    "repository_folder": ("Папка ученика в репозитории", "Путь к материалам ученика"),
    "guardian_table": ("Родители и представители", "Список контактных лиц ученика"),
    "notes": ("Личные заметки об ученике", "Локальные заметки, защищённые Windows DPAPI"),
    "materials_button": ("Открыть материалы ученика", "Перейти к локальному архиву занятий"),
    "save_button": ("Сохранить карточку ученика", "Сохранить все изменения карточки"),
    "grid": ("Недельное расписание", "Стрелками выберите получасовой слот или занятие"),
    "open_selected_button": ("Действие для выбранного слота", "Открыть занятие или создать его в выбранное время"),
    "import_button": ("Создать или импортировать занятие", "Добавить карточку, аудио или транскрипт"),
    "maintenance_button": ("Меню обслуживания архива", "Корзина, диагностика и восстановление"),
    "refresh_button": ("Обновить материалы", "Перечитать список из локальной базы"),
    "student_filter": ("Фильтр материалов по ученику", "Показать занятия выбранного ученика"),
    "subject_filter": ("Фильтр материалов по предмету", "Показать занятия выбранного предмета"),
    "status_filter": ("Фильтр материалов по статусу", "Показать занятия выбранного статуса"),
    "period_enabled": ("Фильтр по периоду", "Включить диапазон дат"),
    "date_from": ("Начало периода материалов", "Первая дата диапазона"),
    "date_to": ("Конец периода материалов", "Последняя дата диапазона"),
    "reset_button": ("Сбросить фильтры материалов", "Показать все занятия"),
    "previous_button": ("Предыдущая страница материалов", "Перейти к предыдущей странице"),
    "next_button": ("Следующая страница материалов", "Перейти к следующей странице"),
    "edit_metadata_button": ("Изменить карточку занятия", "Редактировать ученика, предмет, тему и дату"),
    "delete_lesson_button": ("Переместить занятие в корзину", "Скрыть занятие и переместить его локальные файлы"),
    "content_splitter": ("Список и содержимое материалов", "Левая панель содержит список, правая — выбранное занятие"),
}

_STATUS_NAMES: dict[str, str] = {
    "app_status": "Состояние приложения",
    "quick_readiness_text": "Готовность быстрого урока",
    "recording_state_label": "Состояние записи",
    "recording_health_label": "Состояние аудиопотоков",
    "duration": "Продолжительность записи",
    "dirty_label": "Состояние сохранения карточки ученика",
    "loading_label": "Состояние загрузки материалов",
    "page_label": "Страница списка материалов",
    "details_title": "Содержимое выбранного занятия",
    "publish_summary": "Готовность публикации",
}

_PROGRESS_NAMES: dict[str, tuple[str, str]] = {
    "mic_level": ("Уровень микрофона", "Значение от 0 до 100"),
    "system_level": ("Уровень системного звука", "Значение от 0 до 100"),
    "progress": ("Прогресс транскрибации", "Состояние фоновой операции"),
}


def _install_stylesheet() -> None:
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        return
    if application.property("ux4AccessibilityStyle"):
        return
    application.setStyleSheet(application.styleSheet() + ACCESSIBILITY_STYLESHEET)
    application.setProperty("ux4AccessibilityStyle", True)


def _button_text(widget: QAbstractButton) -> str:
    text = widget.text().replace("&", "").strip()
    if set(text) <= {"·", "•", ".", "…", "⋯"}:
        return ""
    return text


def _fallback_name(widget: QWidget) -> str:
    if isinstance(widget, QAbstractButton):
        text = _button_text(widget)
        if text:
            return text
    if isinstance(widget, QLineEdit) and widget.placeholderText().strip():
        return widget.placeholderText().strip()
    if isinstance(widget, QComboBox) and widget.currentText().strip():
        return widget.currentText().strip()
    if widget.toolTip().strip():
        return widget.toolTip().strip().splitlines()[0]
    if widget.objectName().strip():
        return widget.objectName().replace("_", " ")
    return ""


def _is_interactive(widget: QWidget) -> bool:
    return isinstance(
        widget,
        (
            QAbstractButton,
            QAbstractItemView,
            QAbstractSpinBox,
            QCheckBox,
            QComboBox,
            QDateEdit,
            QLineEdit,
            QPlainTextEdit,
        ),
    )


def _set_widget_metadata(widget: QWidget, name: str, description: str = "") -> None:
    if name:
        widget.setAccessibleName(name)
    if description:
        widget.setAccessibleDescription(description)
    if _is_interactive(widget) and widget.focusPolicy() == Qt.FocusPolicy.NoFocus:
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


def sync_text_status(widget: QWidget, name: str) -> None:
    text = getattr(widget, "text", lambda: "")()
    widget.setAccessibleName(name)
    widget.setAccessibleDescription(str(text).strip())


def set_tab_chain(widgets: Iterable[QWidget | None]) -> None:
    ordered = [widget for widget in widgets if isinstance(widget, QWidget)]
    for first, second in zip(ordered, ordered[1:]):
        QWidget.setTabOrder(first, second)


def _attributes(root: object, names: Iterable[str]) -> list[QWidget | None]:
    return [getattr(root, name, None) for name in names]


def apply_accessibility_to_widget(root: QWidget) -> None:
    _install_stylesheet()
    for attribute, (name, description) in _WIDGET_NAMES.items():
        widget = getattr(root, attribute, None)
        if isinstance(widget, QWidget):
            _set_widget_metadata(widget, name, description)
    for attribute, name in _STATUS_NAMES.items():
        widget = getattr(root, attribute, None)
        if isinstance(widget, QWidget):
            sync_text_status(widget, name)
    for attribute, (name, description) in _PROGRESS_NAMES.items():
        widget = getattr(root, attribute, None)
        if isinstance(widget, QProgressBar):
            _set_widget_metadata(widget, name, description)

    for widget in root.findChildren(QWidget):
        if not _is_interactive(widget):
            continue
        if not widget.accessibleName().strip():
            fallback = _fallback_name(widget)
            if fallback:
                widget.setAccessibleName(fallback)
        if not widget.accessibleDescription().strip() and widget.toolTip().strip():
            widget.setAccessibleDescription(widget.toolTip().strip())
        if widget.focusPolicy() == Qt.FocusPolicy.NoFocus:
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    if hasattr(root, "quick_student"):
        set_tab_chain(
            _attributes(
                root,
                (
                    "quick_student",
                    "quick_topic",
                    "quick_readiness_button",
                    "quick_options_button",
                    "quick_start_button",
                    "detailed_mode_button",
                    "header_more_button",
                ),
            )
        )
    if hasattr(root, "student") and hasattr(root, "loopback"):
        set_tab_chain(
            _attributes(
                root,
                (
                    "student",
                    "subject",
                    "topic",
                    "lesson_date",
                    "mic",
                    "loopback",
                    "audio_output_format",
                    "test_devices_button",
                    "play_mic_test_button",
                    "play_system_test_button",
                    "start_button",
                    "stop_button",
                    "audio_path",
                    "transcribe_button",
                ),
            )
        )
    if hasattr(root, "full_name") and hasattr(root, "guardian_table"):
        set_tab_chain(
            _attributes(
                root,
                (
                    "search",
                    "table",
                    "full_name",
                    "grade",
                    "school",
                    "exam",
                    "goal",
                    "target_score",
                    "subjects",
                    "rate",
                    "active",
                    "technical_toggle",
                    "student_id",
                    "timezone",
                    "repository_folder",
                    "guardian_table",
                    "notes",
                    "materials_button",
                    "save_button",
                ),
            )
        )
    if hasattr(root, "grid") and hasattr(root, "open_selected_button"):
        set_tab_chain(_attributes(root, ("grid", "open_selected_button")))
    if hasattr(root, "student_filter") and hasattr(root, "content_splitter"):
        set_tab_chain(
            _attributes(
                root,
                (
                    "import_button",
                    "maintenance_button",
                    "refresh_button",
                    "student_filter",
                    "subject_filter",
                    "status_filter",
                    "period_enabled",
                    "date_from",
                    "date_to",
                    "search",
                    "reset_button",
                    "table",
                    "previous_button",
                    "next_button",
                    "edit_metadata_button",
                    "delete_lesson_button",
                ),
            )
        )


def install_accessibility(window: QWidget) -> None:
    window.setAccessibleName("Tutor Assistant — рабочее пространство преподавателя")
    window.setAccessibleDescription(
        "Запись занятий, проверка транскриптов, публикация и локальная CRM"
    )
    apply_accessibility_to_widget(window)
    for attribute in (
        "navigation_shell",
        "crm_students_page",
        "crm_schedule_page",
        "student_content_page",
        "transcript_workspace",
    ):
        child = getattr(window, attribute, None)
        if isinstance(child, QWidget):
            apply_accessibility_to_widget(child)
'''


UX4_TESTS = r'''from __future__ import annotations

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
    assert page.table.nextInFocusChain() is page.full_name
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
    assert "16:30–18:00" in item.text()
    assert "Запланировано" in item.text()
    assert page.grid.accessibleName() == "Недельное расписание"
    assert page.grid.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert page.grid.nextInFocusChain() is page.open_selected_button
    page.close()
'''


SCALING_TESTS = r'''from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from tutor_assistant.content import StudentContentService
from tutor_assistant.domain import Lesson, Student
from tutor_assistant.playback import PlaybackController
from tutor_assistant.ui.accessibility import apply_accessibility_to_widget
from tutor_assistant.ui.information_architecture import SidebarNavigation
from tutor_assistant.ui.student_content import StudentContentPage


class FakePlaybackBackend(QObject):
    position_changed = Signal(int)
    duration_changed = Signal(int)
    playing_changed = Signal(bool)
    error_occurred = Signal(str)

    def load(self, _path: Path) -> None:
        self.position_changed.emit(0)

    def play(self) -> None:
        self.playing_changed.emit(True)

    def pause(self) -> None:
        self.playing_changed.emit(False)

    def stop(self) -> None:
        self.playing_changed.emit(False)

    def set_position(self, position_ms: int) -> None:
        self.position_changed.emit(position_ms)

    def position_ms(self) -> int:
        return 0

    def set_rate(self, _rate: float) -> None:
        return

    def is_playing(self) -> bool:
        return False


class FakeScheduler:
    def schedule(self, _delay_ms: int, _callback) -> None:
        return

    def cancel(self) -> None:
        return


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_qt_uses_requested_windows_scale(application: QApplication) -> None:
    requested = float(os.environ.get("QT_SCALE_FACTOR", "1"))
    screen = application.primaryScreen()
    assert screen is not None
    ratio = screen.devicePixelRatio()
    assert ratio >= 1.0
    assert abs(ratio - requested) <= 0.35


def test_sidebar_and_materials_keep_usable_geometry(
    tmp_path: Path,
    application: QApplication,
) -> None:
    tabs = QTabWidget()
    for index in range(8):
        page = QWidget()
        QVBoxLayout(page)
        tabs.addTab(page, str(index))
    navigation = SidebarNavigation(tabs)
    window = QMainWindow()
    window.setCentralWidget(navigation)
    window.resize(1280, 900)
    window.show()
    application.processEvents()

    sidebar = navigation.findChild(QWidget, "sideNavigation")
    assert sidebar is not None
    assert sidebar.width() >= 214
    for button in navigation.ordered_buttons():
        assert button.height() >= 38
        assert button.width() > button.fontMetrics().horizontalAdvance(button.text()) + 20
    window.close()

    service = StudentContentService(tmp_path / "data")
    student = Student(id="student", full_name="Ученик")
    service.create_lesson(
        Lesson(
            lesson_id="ux4-scale",
            student=student,
            subject="mathematics",
            lesson_date=date(2026, 8, 1),
            topic="Windows scaling",
        )
    )
    backend = FakePlaybackBackend()
    controller = PlaybackController(backend, FakeScheduler(), lambda: True)

    def run_background(callable_, succeeded, failed) -> None:
        try:
            succeeded(callable_())
        except Exception as exc:
            failed(str(exc))

    page = StudentContentPage(service, [student], run_background, controller, backend)
    apply_accessibility_to_widget(page)
    page.ensure_loaded()
    page.resize(1280, 900)
    page.show()
    application.processEvents()
    sizes = page.content_splitter.sizes()
    assert len(sizes) == 2
    assert min(sizes) >= 240
    assert page.import_button.height() >= 38
    assert page.table.width() > 300
    page.close()
'''


SCALING_WORKFLOW = r'''name: Windows accessibility scaling

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  scaling:
    runs-on: windows-latest
    timeout-minutes: 25
    strategy:
      fail-fast: false
      matrix:
        scale-factor: ['1', '1.25', '1.5', '2']
    env:
      QT_QPA_PLATFORM: offscreen
      QT_ENABLE_HIGHDPI_SCALING: '1'
      QT_SCALE_FACTOR: ${{ matrix.scale-factor }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install uv
        run: python -m pip install uv

      - name: Check lock file
        run: uv lock --check

      - name: Install desktop and test dependencies
        run: uv sync --extra desktop --group dev

      - name: Accessibility and scaling contracts
        run: >-
          uv run pytest -q --tb=short
          tests/test_ux4_accessibility_gui.py
          tests/test_ux4_scaling_gui.py
'''


Path("src/tutor_assistant/ui/accessibility.py").write_text(
    ACCESSIBILITY,
    encoding="utf-8",
    newline="\n",
)
Path("tests/test_ux4_accessibility_gui.py").write_text(
    UX4_TESTS,
    encoding="utf-8",
    newline="\n",
)
Path("tests/test_ux4_scaling_gui.py").write_text(
    SCALING_TESTS,
    encoding="utf-8",
    newline="\n",
)
Path(".github/workflows/windows-accessibility-scaling.yml").write_text(
    SCALING_WORKFLOW,
    encoding="utf-8",
    newline="\n",
)

replace_once(
    "src/tutor_assistant/ui/information_architecture.py",
    "from PySide6.QtCore import Qt, Signal\n",
    "from PySide6.QtCore import Qt, QTimer, Signal\nfrom PySide6.QtGui import QKeyEvent\n",
)
replace_once(
    "src/tutor_assistant/ui/information_architecture.py",
    "        self.tabs = tabs\n        self.setObjectName(\"informationArchitectureShell\")\n        self.buttons: dict[int, QPushButton] = {}\n        self.quick_button: QPushButton | None = None\n",
    "        self.tabs = tabs\n        self.setObjectName(\"informationArchitectureShell\")\n        self.setAccessibleName(\"Боковая навигация рабочего пространства\")\n        self.setAccessibleDescription(\n            \"Используйте Tab, стрелки, Home, End и Enter для выбора раздела\"\n        )\n        self.buttons: dict[int, QPushButton] = {}\n        self.quick_button: QPushButton | None = None\n        self._button_order: list[QPushButton] = []\n",
)
replace_once(
    "src/tutor_assistant/ui/information_architecture.py",
    "            button.setCursor(Qt.CursorShape.PointingHandCursor)\n            if entry.page_index is None:\n                self.quick_button = button\n                button.clicked.connect(self.quick_requested.emit)\n            else:\n                self.buttons[entry.page_index] = button\n                button.clicked.connect(\n                    lambda _checked=False, index=entry.page_index: self.tabs.setCurrentIndex(index)\n                )\n            sidebar_layout.addWidget(button)\n",
    "            button.setCursor(Qt.CursorShape.PointingHandCursor)\n            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)\n            self._button_order.append(button)\n            if entry.page_index is None:\n                self.quick_button = button\n                button.clicked.connect(\n                    lambda _checked=False, source=button: self._activate_quick(source)\n                )\n            else:\n                self.buttons[entry.page_index] = button\n                button.clicked.connect(\n                    lambda _checked=False, index=entry.page_index, source=button: (\n                        self._activate_page(index, source)\n                    )\n                )\n            sidebar_layout.addWidget(button)\n",
)
replace_once(
    "src/tutor_assistant/ui/information_architecture.py",
    "    def _sync_active(self, current_index: int) -> None:\n",
    "    def _activate_quick(self, source: QPushButton) -> None:\n        self.quick_requested.emit()\n        if source.hasFocus():\n            QTimer.singleShot(0, source.setFocus)\n\n    def _activate_page(self, index: int, source: QPushButton) -> None:\n        self.tabs.setCurrentIndex(index)\n        if source.hasFocus():\n            QTimer.singleShot(0, self._focus_current_page)\n\n    def _focus_current_page(self) -> None:\n        page = self.tabs.currentWidget()\n        if page is None:\n            return\n        for candidate in page.findChildren(QWidget):\n            if (\n                candidate.isVisibleTo(page)\n                and candidate.isEnabled()\n                and candidate.focusPolicy() != Qt.FocusPolicy.NoFocus\n            ):\n                candidate.setFocus(Qt.FocusReason.TabFocusReason)\n                return\n        page.setFocus(Qt.FocusReason.TabFocusReason)\n\n    def keyPressEvent(self, event: QKeyEvent) -> None:\n        focused = QApplication.focusWidget()\n        if focused not in self._button_order:\n            super().keyPressEvent(event)\n            return\n        current = self._button_order.index(focused)\n        key = event.key()\n        if key in {Qt.Key.Key_Down, Qt.Key.Key_Right}:\n            target = self._button_order[(current + 1) % len(self._button_order)]\n        elif key in {Qt.Key.Key_Up, Qt.Key.Key_Left}:\n            target = self._button_order[(current - 1) % len(self._button_order)]\n        elif key == Qt.Key.Key_Home:\n            target = self._button_order[0]\n        elif key == Qt.Key.Key_End:\n            target = self._button_order[-1]\n        elif key in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:\n            focused.click()\n            event.accept()\n            return\n        else:\n            super().keyPressEvent(event)\n            return\n        target.setFocus(Qt.FocusReason.TabFocusReason)\n        event.accept()\n\n    def ordered_buttons(self) -> tuple[QPushButton, ...]:\n        return tuple(self._button_order)\n\n    def _sync_active(self, current_index: int) -> None:\n",
)

replace_once(
    "src/tutor_assistant/ui/transcript_publication_app.py",
    "from . import app as base_app\n",
    "from . import app as base_app\nfrom .accessibility import install_accessibility\n",
)
replace_once(
    "src/tutor_assistant/ui/transcript_publication_app.py",
    "        self.navigation_shell = install_information_architecture(self)\n        self._set_mode(\"quick\" if quick_mode else \"detailed\")\n",
    "        self.navigation_shell = install_information_architecture(self)\n        self._set_mode(\"quick\" if quick_mode else \"detailed\")\n        install_accessibility(self)\n",
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    "from .crm import SchedulePage, StudentsPage\n",
    "from .accessibility import sync_text_status\nfrom .crm import SchedulePage, StudentsPage\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "        set_status(self.app_status, message, tone)\n        self.statusBar().showMessage(message)\n",
    "        set_status(self.app_status, message, tone)\n        sync_text_status(self.app_status, \"Состояние приложения\")\n        self.statusBar().setAccessibleName(\"Строка состояния приложения\")\n        self.statusBar().setAccessibleDescription(message)\n        self.statusBar().showMessage(message)\n",
)
replace_once(
    "src/tutor_assistant/ui/app.py",
    "        self.quick_readiness_text.setText(readiness_text)\n        self.quick_readiness_text.setProperty(\"tone\", \"ready\" if readiness.ready else \"blocked\")\n",
    "        self.quick_readiness_text.setText(readiness_text)\n        self.quick_readiness_text.setProperty(\"tone\", \"ready\" if readiness.ready else \"blocked\")\n        sync_text_status(self.quick_readiness_text, \"Готовность быстрого урока\")\n        self.quick_readiness_button.setAccessibleDescription(readiness_text)\n",
)

replace_once(
    "src/tutor_assistant/ui/crm.py",
    "from .localization import (\n",
    "from .accessibility import sync_text_status\nfrom .localization import (\n",
)
replace_once(
    "src/tutor_assistant/ui/crm.py",
    "        self.save_button.setEnabled(dirty)\n        refresh_style(self.dirty_label)\n",
    "        self.save_button.setEnabled(dirty)\n        sync_text_status(self.dirty_label, \"Состояние сохранения карточки ученика\")\n        refresh_style(self.dirty_label)\n",
)

replace_once(
    "src/tutor_assistant/ui/student_content.py",
    "from .localization import subject_label, subject_value\n",
    "from .accessibility import sync_text_status\nfrom .localization import subject_label, subject_value\n",
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    "        self.details_title.setText(lesson.topic or \"Содержимое занятия\")\n",
    "        self.details_title.setText(lesson.topic or \"Содержимое занятия\")\n        sync_text_status(self.details_title, \"Содержимое выбранного занятия\")\n",
)
replace_once(
    "src/tutor_assistant/ui/student_content.py",
    "            self.details_title.setText(\"Выберите занятие\")\n",
    "            self.details_title.setText(\"Выберите занятие\")\n            sync_text_status(self.details_title, \"Содержимое выбранного занятия\")\n",
)

replace_once(
    "pyproject.toml",
    'version = "0.21.0"',
    'version = "0.22.0"',
)
replace_once(
    "src/tutor_assistant/__init__.py",
    '__version__ = "0.21.0"',
    '__version__ = "0.22.0"',
)
replace_once(
    "README.md",
    "Текущая версия: **0.21.0**.",
    "Текущая версия: **0.22.0**.",
)
readme = Path("README.md")
readme_content = readme.read_text(encoding="utf-8")
ux4_section = """

## UX-4: доступность

- единый контрастный focus ring охватывает кнопки, поля, таблицы, списки и переключатели;
- ключевые элементы быстрого урока, CRM, расписания и материалов получили accessible names и descriptions;
- Tab order закреплён по визуальному порядку основных сценариев;
- боковая навигация поддерживает стрелки, Home, End, Enter и Space;
- цветовые состояния сопровождаются текстовыми формулировками для средств доступности;
- отдельная Windows-матрица проверяет интерфейс при масштабах 100%, 125%, 150% и 200%.
"""
if "## UX-4: доступность" not in readme_content:
    readme.write_text(readme_content + ux4_section, encoding="utf-8", newline="\n")

plan = Path("docs/ux4-accessibility.md")
plan.write_text(
    """# UX-4: доступность

## Реализованные контракты

1. Единый focus ring применяется к кнопкам, полям, таблицам, спискам, checkbox и spinbox.
2. Основные контролы быстрого урока, подготовки занятия, CRM, расписания и материалов имеют осмысленные accessible names и descriptions.
3. Tab order следует визуальному порядку рабочих сценариев.
4. Боковая навигация поддерживает Tab, стрелки, Home, End, Enter и Space.
5. Готовность, запись, dirty-state, публикация и загрузка материалов представлены текстовыми статусами.
6. Windows scaling проверяется при коэффициентах 1.0, 1.25, 1.5 и 2.0.
7. UI-тесты проверяют keyboard-only сценарии, focus policy, доступные имена и DPI-safe geometry.
""",
    encoding="utf-8",
    newline="\n",
)
