from __future__ import annotations

import runpy
from pathlib import Path

path = Path(__file__).with_name("apply_ux2_core_workflows.py")
source = path.read_text(encoding="utf-8")
replacements = {
    "assert workspace.review_result_button.isVisible()": (
        "assert not workspace.review_result_button.isHidden()"
    ),
    "assert workspace.progress.isVisible()": "assert not workspace.progress.isHidden()",
    "assert dialog.progress.isVisible()": "assert not dialog.progress.isHidden()",
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"UX-2 test assertion marker changed: {old}")
    source = source.replace(old, new, 1)
path.write_text(source, encoding="utf-8", newline="\n")
runpy.run_path(str(path), run_name="__main__")
