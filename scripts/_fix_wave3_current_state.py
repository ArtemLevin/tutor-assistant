from pathlib import Path

path = Path("PLAN.md")
text = path.read_text(encoding="utf-8")
old = (
    "Wave 2 архитектурной стабилизации production-контура записи завершён. "
    "В Wave 3 завершён первый slice: orchestration локальной очереди транскрибации "
    "отделена от базового Qt-окна. Следующий фокус — LLM normalization orchestration."
)
new = (
    "Wave 2 архитектурной стабилизации production-контура записи завершён. "
    "В Wave 3 завершены Slices 13–14: orchestration очереди транскрибации и LLM normalization "
    "отделены от базового Qt-окна. Следующий фокус — LaTeX monitor UI orchestration."
)
if text.count(old) != 1:
    raise RuntimeError(f"expected current-state paragraph once, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
