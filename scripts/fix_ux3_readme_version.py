from pathlib import Path

path = Path("README.md")
content = path.read_text(encoding="utf-8")
old = "Текущая версия: **0.19.0**."
new = "Текущая версия: **0.21.0**."
if content.count(old) != 1:
    raise RuntimeError("README version marker was not found exactly once")
path.write_text(content.replace(old, new, 1), encoding="utf-8", newline="\n")
