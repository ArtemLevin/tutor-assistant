from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
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
    "quick_readiness_button": (
        "Открыть подробную проверку готовности",
        "Показывает состояние данных и аудиоустройств",
    ),
    "quick_options_button": ("Настройки быстрого урока", "Открыть выбор профиля и предмета"),
    "quick_start_button": (
        "Основное действие быстрого урока",
        "Начать, отменить запуск или завершить занятие",
    ),
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
    "open_selected_button": (
        "Действие для выбранного слота",
        "Открыть занятие или создать его в выбранное время",
    ),
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
    "delete_lesson_button": (
        "Переместить занятие в корзину",
        "Скрыть занятие и переместить его локальные файлы",
    ),
    "content_splitter": (
        "Список и содержимое материалов",
        "Левая панель содержит список, правая — выбранное занятие",
    ),
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
    for first, second in zip(ordered, ordered[1:], strict=False):
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
        if isinstance(widget, QAbstractItemView):
            widget.setTabKeyNavigation(False)
        if isinstance(widget, QAbstractButton):
            widget.setMinimumHeight(max(widget.minimumHeight(), 38))
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
    window.setAccessibleDescription("Запись занятий, проверка транскриптов, публикация и локальная CRM")
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
