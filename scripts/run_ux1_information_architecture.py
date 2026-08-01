from __future__ import annotations

import runpy
from pathlib import Path

path = Path(__file__).with_name("apply_ux1_information_architecture.py")
source = path.read_text(encoding="utf-8")

small = '''replace_once(
    "src/tutor_assistant/ui/app.py",
    "        subject_index = self.quick_subject.findText(subject)\\n        if subject_index >= 0:\\n            self.quick_subject.setCurrentIndex(subject_index)\\n",
    "        select_subject(self.quick_subject, subject)\\n",
)
'''
large = '''replace_once(
    "src/tutor_assistant/ui/app.py",
    "        self.quick_subject = QComboBox()\\n        self.quick_subject.setToolTip(\\"Предмет определяет папку и шаблоны материалов\\")\\n        self.quick_subject.addItems([\\"mathematics\\", \\"physics\\", \\"chemistry\\"])\\n        subject = self.config.quick_start.last_subject or profile.subject\\n        subject_index = self.quick_subject.findText(subject)\\n        if subject_index >= 0:\\n            self.quick_subject.setCurrentIndex(subject_index)\\n",
    "        self.quick_subject = QComboBox()\\n        self.quick_subject.setToolTip(\\"Предмет определяет папку и шаблоны материалов\\")\\n        set_subject_combo(\\n            self.quick_subject,\\n            selected=self.config.quick_start.last_subject or profile.subject,\\n        )\\n",
)
'''

if source.count(small) != 1 or source.count(large) != 1:
    raise RuntimeError("UX-1 bootstrap patch order markers changed")
source = source.replace(small, "", 1)
source = source.replace(large, large + small, 1)
path.write_text(source, encoding="utf-8", newline="\n")
runpy.run_path(str(path), run_name="__main__")
