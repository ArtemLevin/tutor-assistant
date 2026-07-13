from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def validate_file(instance_path: Path, schema_path: Path) -> list[str]:
    instance = json.loads(instance_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance),
        key=lambda error: list(error.path),
    )
    return [
        f"{instance_path.name}:{'.'.join(map(str, error.path)) or '<root>'}: {error.message}"
        for error in errors
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("lesson_json", type=Path)
    parser.add_argument("--schema", type=Path, default=Path("schemas/lesson.schema.json"))
    parser.add_argument(
        "--job-schema",
        type=Path,
        default=Path("schemas/job-status.schema.json"),
    )
    args = parser.parse_args()
    errors = validate_file(args.lesson_json, args.schema)
    job_status = args.lesson_json.with_name("job.status.json")
    if job_status.exists():
        errors.extend(validate_file(job_status, args.job_schema))
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(1)
    suffix = " and job.status.json" if job_status.exists() else ""
    print(f"lesson.json{suffix} valid")


if __name__ == "__main__":
    main()
