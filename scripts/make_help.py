from __future__ import annotations

import re
from pathlib import Path


def main() -> None:
    makefile = Path(__file__).resolve().parents[1] / "Makefile"
    entries: list[tuple[str, str]] = []
    pattern = re.compile(r"^([a-zA-Z0-9_-]+):.*?##\s*(.+)$")
    for line in makefile.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            entries.append((match.group(1), match.group(2)))
    width = max((len(name) for name, _ in entries), default=0)
    print("Tutor Assistant — команды Make/uv\n")
    for name, description in entries:
        print(f"  make {name:<{width}}  {description}")
    print("\nПараметры: CONFIG=config/app.yaml TEX=... LESSON=... RECORDING=...")


if __name__ == "__main__":
    main()
