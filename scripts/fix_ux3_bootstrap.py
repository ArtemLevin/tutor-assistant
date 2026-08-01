from pathlib import Path

path = Path(__file__).with_name("apply_ux3_crm_materials.py")
content = path.read_text(encoding="utf-8")

regex_old = '''    updated, count = re.subn(pattern, replacement, content, count=1, flags=re.S)
'''
regex_new = '''    updated, count = re.subn(
        pattern,
        lambda _match: replacement,
        content,
        count=1,
        flags=re.S,
    )
'''
if content.count(regex_old) != 1:
    raise RuntimeError("UX-3 regex replacement helper was not found exactly once")
content = content.replace(regex_old, regex_new, 1)

refresh_old = '''replace_once(
    "src/tutor_assistant/ui/crm.py",
    \'\'\'    def refresh(self) -> None:
\'\'\',
    \'\'\'    def _connect_dirty_tracking(self) -> None:
'''
refresh_new = '''replace_once(
    "src/tutor_assistant/ui/crm.py",
    \'\'\'        self._connect_dirty_tracking()

    def refresh(self) -> None:
\'\'\',
    \'\'\'        self._connect_dirty_tracking()

    def _connect_dirty_tracking(self) -> None:
'''
if content.count(refresh_old) != 1:
    raise RuntimeError("UX-3 StudentsPage refresh marker was not found exactly once")
content = content.replace(refresh_old, refresh_new, 1)

readme_old = '''replace_once(
    "README.md",
    "## ",
    dedent(
        \'\'\'\\
        ## UX-3: CRM и материалы

        - Карточка ученика показывает dirty-state и защищает изменения при переключении.
        - Учебные поля отделены от раскрываемых технических параметров.
        - Материалы работают в split view со списком и постоянной панелью содержимого.
        - Корзина, диагностика и восстановление собраны в меню обслуживания архива.
        - Расписание использует 30-минутную сетку и отображает длительность занятия высотой блока.

        ## \'\'\'
    ),
)
'''
readme_new = '''replace_once(
    "README.md",
    "## LLM-фильтрация учебного содержания\\n",
    dedent(
        \'\'\'\\
        ## UX-3: CRM и материалы

        - Карточка ученика показывает dirty-state и защищает изменения при переключении.
        - Учебные поля отделены от раскрываемых технических параметров.
        - Материалы работают в split view со списком и постоянной панелью содержимого.
        - Корзина, диагностика и восстановление собраны в меню обслуживания архива.
        - Расписание использует 30-минутную сетку и отображает длительность занятия высотой блока.

        ## LLM-фильтрация учебного содержания
        \'\'\'
    ),
)
'''
if content.count(readme_old) != 1:
    raise RuntimeError("UX-3 README insertion marker was not found exactly once")
content = content.replace(readme_old, readme_new, 1)

test_import_old = '''    from PySide6.QtWidgets import QApplication, QMessageBox
'''
test_import_new = '''    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication, QMessageBox
'''
if content.count(test_import_old) != 1:
    raise RuntimeError("UX-3 playback test import marker was not found exactly once")
content = content.replace(test_import_old, test_import_new, 1)

test_backend_old = '''    class FakePlaybackBackend:
        def load(self, _path: Path) -> None:
            return

        def play(self) -> None:
            return

        def pause(self) -> None:
            return

        def stop(self) -> None:
            return

        def set_position(self, _position_ms: int) -> None:
            return

        def position_ms(self) -> int:
            return 0

        def set_rate(self, _rate: float) -> None:
            return

        def is_playing(self) -> bool:
            return False
'''
test_backend_new = '''    class FakePlaybackBackend(QObject):
        position_changed = Signal(int)
        duration_changed = Signal(int)
        playing_changed = Signal(bool)
        error_occurred = Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self.position = 0
            self.playing = False

        def load(self, _path: Path) -> None:
            self.position = 0
            self.position_changed.emit(0)

        def play(self) -> None:
            self.playing = True
            self.playing_changed.emit(True)

        def pause(self) -> None:
            self.playing = False
            self.playing_changed.emit(False)

        def stop(self) -> None:
            self.pause()

        def set_position(self, position_ms: int) -> None:
            self.position = position_ms
            self.position_changed.emit(position_ms)

        def position_ms(self) -> int:
            return self.position

        def set_rate(self, _rate: float) -> None:
            return

        def is_playing(self) -> bool:
            return self.playing
'''
if content.count(test_backend_old) != 1:
    raise RuntimeError("UX-3 fake playback backend marker was not found exactly once")
content = content.replace(test_backend_old, test_backend_new, 1)

path.write_text(content, encoding="utf-8", newline="\n")
