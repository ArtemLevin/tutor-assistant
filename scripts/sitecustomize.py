from __future__ import annotations

import sys
from pathlib import Path

if Path(sys.argv[0]).name == "apply_ux4_patch.py":
    patch = Path(__file__).with_name("apply_ux4_patch.py")
    content = patch.read_text(encoding="utf-8")
    old = (
        '    "from .localization import subject_label, subject_value\\n",\n'
        '    "from .accessibility import sync_text_status\\n'
        'from .localization import subject_label, subject_value\\n",\n'
    )
    new = (
        '    "from .localization import subject_label\\n",\n'
        '    "from .accessibility import sync_text_status\\n'
        'from .localization import subject_label\\n",\n'
    )
    if content.count(old) != 1:
        raise RuntimeError("UX-4 student-content import marker is ambiguous")
    patch.write_text(content.replace(old, new, 1), encoding="utf-8", newline="\n")
    Path(__file__).unlink()
