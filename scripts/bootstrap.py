from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Подготовить локальную конфигурацию Tutor Assistant")
    parser.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    parser.add_argument("--example", type=Path, default=Path("config/app.example.yaml"))
    parser.add_argument("--students", type=Path, default=Path("config/students.yaml"))
    parser.add_argument(
        "--students-example",
        type=Path,
        default=Path("config/students.example.yaml"),
    )
    args = parser.parse_args()

    created: list[Path] = []
    for destination, example in (
        (args.config, args.example),
        (args.students, args.students_example),
    ):
        if destination.exists():
            continue
        if not example.is_file():
            raise SystemExit(f"Шаблон конфигурации отсутствует: {example}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(example, destination)
        created.append(destination)

    if created:
        print("Созданы локальные файлы: " + ", ".join(str(path) for path in created))
    else:
        print("Локальная конфигурация уже существует")


if __name__ == "__main__":
    main()
