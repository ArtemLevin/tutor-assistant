from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .config import AppConfig, load_students
from .domain import Lesson
from .pipeline import LessonPipeline
from .recording import list_input_devices


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tutor-assistant")
    root.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("devices", help="Показать входные аудиоустройства")
    create = commands.add_parser("create", help="Создать занятие")
    create.add_argument("--student", required=True)
    create.add_argument("--subject", required=True)
    create.add_argument("--topic", required=True)
    create.add_argument("--date", default=date.today().isoformat())
    transcribe = commands.add_parser("transcribe", help="Транскрибировать аудио")
    transcribe.add_argument("lesson_json", type=Path)
    transcribe.add_argument("audio", type=Path)
    publish = commands.add_parser("publish", help="Опубликовать подтверждённое занятие")
    publish.add_argument("lesson_json", type=Path)
    return root


def main() -> None:
    args = parser().parse_args()
    config = AppConfig.load(args.config)
    if args.command == "devices":
        print(json.dumps([device.__dict__ for device in list_input_devices()], ensure_ascii=False, indent=2))
        return
    pipeline = LessonPipeline(config)
    if args.command == "create":
        students = {item.id: item for item in load_students(config.students_file)}
        lesson = Lesson(
            student=students[args.student], subject=args.subject,
            lesson_date=date.fromisoformat(args.date), topic=args.topic,
        )
        print(pipeline.create(lesson) / "lesson.json")
    elif args.command == "transcribe":
        lesson = Lesson.read_json(args.lesson_json)
        pipeline.transcribe(lesson, args.audio)
        print(pipeline.lesson_dir(lesson) / "lesson.json")
    elif args.command == "publish":
        lesson = Lesson.read_json(args.lesson_json)
        print(pipeline.publish(lesson))


if __name__ == "__main__":
    main()

