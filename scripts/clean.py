from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = (".pytest_cache", ".ruff_cache", "build", "dist")


def main() -> None:
    removed: list[Path] = []
    candidates = [ROOT / name for name in DIRECTORIES]
    candidates.extend(ROOT.rglob("__pycache__"))
    candidates.extend(ROOT.glob("*.egg-info"))
    candidates.extend((ROOT / "src").glob("*.egg-info"))
    for path in sorted(set(candidates), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(path.relative_to(ROOT))
    print(f"Удалено каталогов: {len(removed)}")


if __name__ == "__main__":
    main()
