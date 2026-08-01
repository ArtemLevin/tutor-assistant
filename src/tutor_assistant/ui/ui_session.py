from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QByteArray, QDate, QEvent, QObject, QRect, QSettings, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QComboBox, QMainWindow, QSplitter

from .app_routes import AppRoute, parse_route


class UISessionStore(QObject):
    """Versioned persistence for window and workspace continuity."""

    schema_version = 1

    def __init__(
        self,
        window: QMainWindow,
        *,
        settings: QSettings | None = None,
        route_provider: Callable[[], AppRoute] | None = None,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.settings = settings or QSettings("TutorAssistant", "TutorAssistant")
        self.route_provider = route_provider
        self._splitters: dict[str, QSplitter] = {}
        self.window.installEventFilter(self)

    @property
    def stored_schema(self) -> int:
        try:
            return int(self.settings.value("ux6/schema", 0))
        except (TypeError, ValueError):
            return 0

    @property
    def schema_is_current(self) -> bool:
        return self.stored_schema == self.schema_version

    @property
    def initialized(self) -> bool:
        return self.schema_is_current and bool(
            self.settings.value("ux6/initialized", False, type=bool)
        )

    def mark_initialized(self) -> None:
        self.settings.setValue("ux6/schema", self.schema_version)
        self.settings.setValue("ux6/initialized", True)

    def reset_incompatible(self) -> bool:
        if self.stored_schema in {0, self.schema_version}:
            return False
        self.settings.remove("ux6")
        self.settings.sync()
        return True

    def register_splitter(self, name: str, splitter: QSplitter | None) -> None:
        if splitter is None:
            return
        splitter.setObjectName(splitter.objectName() or name)
        self._splitters[name] = splitter
        splitter.splitterMoved.connect(lambda _position, _index: self.save_splitters())

    @staticmethod
    def _available_geometries() -> list[QRect]:
        return [screen.availableGeometry() for screen in QGuiApplication.screens()]

    def _ensure_window_is_visible(self) -> None:
        available = self._available_geometries()
        if not available:
            return
        frame = self.window.frameGeometry()
        if any(rect.intersects(frame) for rect in available):
            return
        target = QGuiApplication.primaryScreen()
        bounds = target.availableGeometry() if target is not None else available[0]
        width = min(max(self.window.width(), 720), bounds.width())
        height = min(max(self.window.height(), 520), bounds.height())
        x = bounds.x() + max(0, (bounds.width() - width) // 2)
        y = bounds.y() + max(0, (bounds.height() - height) // 2)
        self.window.setGeometry(x, y, width, height)

    def restore_window(self) -> None:
        self.reset_incompatible()
        if not self.schema_is_current:
            return
        geometry = self.settings.value("ux6/window_geometry")
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            self.window.restoreGeometry(geometry)
            self._ensure_window_is_visible()

    def save_window(self) -> None:
        geometry = self.window.saveGeometry()
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            self.settings.setValue("ux6/window_geometry", geometry)

    def restore_splitters(self) -> None:
        if not self.schema_is_current:
            return
        for name, splitter in self._splitters.items():
            state = self.settings.value(f"ux6/splitters/{name}")
            if isinstance(state, QByteArray) and not state.isEmpty():
                splitter.restoreState(state)

    def save_splitters(self) -> None:
        for name, splitter in self._splitters.items():
            self.settings.setValue(f"ux6/splitters/{name}", splitter.saveState())

    def preferred_route(self, default: AppRoute = AppRoute.TODAY) -> AppRoute:
        if not self.schema_is_current:
            return default
        return parse_route(self.settings.value("ux6/last_route", default.value), default)

    def record_route(self, route: AppRoute | str) -> None:
        parsed = parse_route(route)
        if parsed != AppRoute.QUICK_LESSON:
            self.settings.setValue("ux6/last_route", parsed.value)

    def preferred_mode(self, default: str) -> str:
        if not self.schema_is_current:
            return default
        value = str(self.settings.value("ux6/mode", default))
        return value if value in {"quick", "detailed"} else default

    def record_mode(self, mode: str) -> None:
        if mode in {"quick", "detailed"}:
            self.settings.setValue("ux6/mode", mode)

    def sidebar_collapsed(self, default: bool = False) -> bool:
        if not self.schema_is_current:
            return default
        return bool(self.settings.value("ux6/sidebar_collapsed", default, type=bool))

    def record_sidebar_collapsed(self, collapsed: bool) -> None:
        self.settings.setValue("ux6/sidebar_collapsed", collapsed)

    @staticmethod
    def _combo_value(combo: QComboBox) -> str:
        data = combo.currentData()
        return "" if data is None else str(data)

    @staticmethod
    def _restore_combo(combo: QComboBox, value: str) -> None:
        if not value:
            combo.setCurrentIndex(0)
            return
        index = combo.findData(value)
        if index < 0:
            index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def save_material_filters(self, page: object) -> None:
        for name in ("student_filter", "subject_filter", "status_filter"):
            combo = getattr(page, name, None)
            if isinstance(combo, QComboBox):
                self.settings.setValue(
                    f"ux6/materials/{name}",
                    self._combo_value(combo),
                )
        period = getattr(page, "period_enabled", None)
        if period is not None and hasattr(period, "isChecked"):
            self.settings.setValue(
                "ux6/materials/period_enabled",
                bool(period.isChecked()),
            )
        for name in ("date_from", "date_to"):
            editor = getattr(page, name, None)
            if editor is not None and hasattr(editor, "date"):
                self.settings.setValue(
                    f"ux6/materials/{name}",
                    editor.date().toString("yyyy-MM-dd"),
                )

    def restore_material_filters(self, page: object) -> None:
        if not self.schema_is_current:
            return
        for name in ("student_filter", "subject_filter", "status_filter"):
            combo = getattr(page, name, None)
            if isinstance(combo, QComboBox):
                value = str(self.settings.value(f"ux6/materials/{name}", ""))
                self._restore_combo(combo, value)
        period = getattr(page, "period_enabled", None)
        if period is not None and hasattr(period, "setChecked"):
            period.setChecked(
                bool(
                    self.settings.value(
                        "ux6/materials/period_enabled",
                        False,
                        type=bool,
                    )
                )
            )
        for name in ("date_from", "date_to"):
            editor = getattr(page, name, None)
            value = str(self.settings.value(f"ux6/materials/{name}", ""))
            parsed = QDate.fromString(value, "yyyy-MM-dd")
            if editor is not None and parsed.isValid() and hasattr(editor, "setDate"):
                editor.setDate(parsed)

    def restore_deferred(self, materials_page: object | None = None) -> None:
        def restore() -> None:
            self.restore_splitters()
            if materials_page is not None:
                self.restore_material_filters(materials_page)

        QTimer.singleShot(0, restore)

    def save_all(self) -> None:
        self.save_window()
        self.save_splitters()
        materials_page = getattr(self.window, "student_content_page", None)
        if materials_page is not None:
            self.save_material_filters(materials_page)
        if self.route_provider is not None:
            self.record_route(self.route_provider())
        self.mark_initialized()
        self.settings.sync()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.window and event.type() == QEvent.Type.Close:
            self.save_all()
        return super().eventFilter(watched, event)
