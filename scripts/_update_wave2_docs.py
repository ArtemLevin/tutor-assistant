from __future__ import annotations

from pathlib import Path


README = Path("README.md")
PLAN = Path("PLAN.md")

readme = README.read_text(encoding="utf-8")
readme = readme.replace(
    "К текущему состоянию завершены P0-стабилизация и **Wave 2 / Slices 1–11**:",
    "К текущему состоянию завершены P0-стабилизация и **Wave 2 / Slices 1–12**:",
    1,
)
needle = (
    "- Qt-free `recording_presentation` model для duration, level normalization, "
    "health summary, warning/recovery cues и canonical recording-panel phases.\n"
)
if needle not in readme:
    raise RuntimeError("README Wave 2 bullet anchor not found")
readme = readme.replace(
    needle,
    needle
    + "- explicit production composition: общий bootstrap получает `window_type`, а production adapters больше не меняют `base_app.MainWindow` через module-global rebinding; responsibility-bearing MRO закреплён architecture tests.\n",
    1,
)
old_next = (
    "Следующий архитектурный шаг — **Wave 2 / Slice 12: Production composition cleanup**. "
    "Recording policy и presentation state уже отделены от base UI; следующий slice упрощает "
    "production MRO/composition root и удаляет ставшие ненужными compatibility bridges. "
    "Детали и критерии готовности описаны в [`PLAN.md`](PLAN.md)."
)
new_next = (
    "**Wave 2 завершён.** Следующий архитектурный шаг — **Wave 3 / Slice 13: "
    "Transcription queue presentation/orchestration extraction**. Цель — вынести из `ui/app.py` "
    "координацию очереди транскрибации и её presentation state за Qt-free boundary, не затрагивая "
    "recording safety path. Детали и критерии готовности описаны в [`PLAN.md`](PLAN.md)."
)
if old_next not in readme:
    raise RuntimeError("README next-step paragraph not found")
readme = readme.replace(old_next, new_next, 1)
old_bottom = (
    "На текущем этапе приоритет — завершить Wave 2 через production composition cleanup, затем "
    "перейти к декомпозиции transcription, normalization, LaTeX и shutdown orchestration."
)
new_bottom = (
    "На текущем этапе Wave 2 завершён. Приоритет Wave 3 — последовательно декомпозировать "
    "transcription queue, normalization, LaTeX и shutdown orchestration, начиная с очереди транскрибации."
)
if old_bottom not in readme:
    raise RuntimeError("README roadmap footer not found")
readme = readme.replace(old_bottom, new_bottom, 1)
README.write_text(readme, encoding="utf-8")

plan = PLAN.read_text(encoding="utf-8")n