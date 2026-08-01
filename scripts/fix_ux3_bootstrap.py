from pathlib import Path

path = Path(__file__).with_name("apply_ux3_crm_materials.py")
content = path.read_text(encoding="utf-8")

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

path.write_text(content, encoding="utf-8", newline="\n")
