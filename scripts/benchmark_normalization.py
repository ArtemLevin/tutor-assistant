from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from time import perf_counter

from tutor_assistant.config import AppConfig
from tutor_assistant.normalization import NormalizationService
from tutor_assistant.pipeline import LessonPipeline


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Sequential local benchmark for transcript normalization models"
    )
    command.add_argument("lesson_id")
    command.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    command.add_argument(
        "--models",
        nargs="+",
        default=["qwen3:8b", "qwen3:14b"],
    )
    command.add_argument("--repeats", type=int, default=1)
    command.add_argument("--output", type=Path)
    return command


def main() -> None:
    args = parser().parse_args()
    if args.repeats < 1:
        raise SystemExit("--repeats must be positive")
    config = AppConfig.load(args.config)
    pipeline = LessonPipeline(config)
    service = NormalizationService(config.normalization, pipeline.content_service)
    rows = []
    for model in args.models:
        samples = []
        for _ in range(args.repeats):
            started = perf_counter()
            result = service.normalize_lesson(
                args.lesson_id,
                model=model,
                dry_run=True,
            )
            samples.append(
                {
                    "elapsed_seconds": round(perf_counter() - started, 3),
                    "retained_ratio": result.transcript.statistics.retained_ratio,
                    "warnings": len(result.transcript.quality.warnings),
                }
            )
        rows.append(
            {
                "model": model,
                "repeats": args.repeats,
                "mean_elapsed_seconds": round(
                    mean(item["elapsed_seconds"] for item in samples),
                    3,
                ),
                "samples": samples,
            }
        )
    payload = {
        "lesson_id": args.lesson_id,
        "execution": "sequential",
        "temperature": config.normalization.temperature,
        "results": rows,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
