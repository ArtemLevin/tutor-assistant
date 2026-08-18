from pathlib import Path

path = Path("PLAN.md")
text = path.read_text(encoding="utf-8")
old = "## 7. Инварианты разработки\n## 6. Инварианты разработки\n"
if text.count(old) != 1:
    raise RuntimeError(f"expected duplicate heading once, found {text.count(old)}")
text = text.replace(old, "## 7. Инварианты разработки\n", 1)
path.write_text(text, encoding="utf-8")
