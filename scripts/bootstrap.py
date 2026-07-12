from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Подготовить локальную конфигурацию Tutor Assistant")
    parser.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    parser.add_argument("--example", type=Path, default=Path("config/app.example.yaml"))
    args = parser.parse_args()

    if args.config.exists():
        print(f"Конфигурация уже существует: {args.config}")
        return
    if not args.example.is_file():
        raise SystemExit(f"Шаблон конфигурации отсутствует: {args.example}")
    args.config.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.example, args.config)
    print(f"Создана конфигурация: {args.config}")


if __name__ == "__main__":
    main()
