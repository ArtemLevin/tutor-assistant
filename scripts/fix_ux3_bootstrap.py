from pathlib import Path

path = Path(__file__).with_name("apply_ux3_crm_materials.py")
content = path.read_text(encoding="utf-8")
old = '''replace_once(
    "src/tutor_assistant/ui/crm.py",
    \'\'\'    def refresh(self) -> None:
\'\'\',
    \'\'\'    def _connect_dirty_tracking(self) -> None:
'''
new = '''replace_once(
    "src/tutor_assistant/ui/crm.py",
    \'\'\'        self._connect_dirty_tracking()

    def refresh(self) -> None:
\'\'\',
    \'\'\'        self._connect_dirty_tracking()

    def _connect_dirty_tracking(self) -> None:
'''
if content.count(old) != 1:
    raise RuntimeError("UX-3 StudentsPage refresh marker was not found exactly once")
path.write_text(content.replace(old, new, 1), encoding="utf-8", newline="\n")
