from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    content = target.read_text(encoding="utf-8")
    if content.count(old) != 1:
        raise RuntimeError(f"Expected exactly one occurrence in {path}: {old!r}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once("pyproject.toml", 'version = "0.13.0"', 'version = "0.13.1"')
replace_once("README.md", "Текущая версия: **0.13.0**.", "Текущая версия: **0.13.1**.")
