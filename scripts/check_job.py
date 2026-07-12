from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("lesson_json", type=Path)
    parser.add_argument("--schema", type=Path, default=Path("schemas/lesson.schema.json"))
    args = parser.parse_args()
    instance = json.loads(args.lesson_json.read_text(encoding="utf-8"))
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance),
        key=lambda error: list(error.path),
    )
    if errors:
        for error in errors:
            print(f"{'.'.join(map(str, error.path))}: {error.message}")
        raise SystemExit(1)
    print("lesson.json valid")


if __name__ == "__main__":
    main()
