from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .config import AppConfig, load_students
from .domain import Lesson
from .pipeline import LessonPipeline
from .recording import list_input_devices, list_system_audio_sources


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tutor-assistant")
    root.add_argument("--config", type=Path, default=Path("config/app.yaml"))
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("devices", help="Показать входные аудиоустройства")
    doctor = commands.add_parser("doctor", help="Проверить всё окружение приложения")
    doctor.add_argument("--json", action="store_true", help="Вывести машиночитаемый JSON")
    doctor.add_argument("--strict", action="store_true", help="Вернуть код 1 при обязательных ошибках")
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
    commands.add_parser("latex-doctor", help="Проверить локальное LaTeX-окружение")
    compile_tex = commands.add_parser("compile", help="Безопасно скомпилировать локальный TEX")
    compile_tex.add_argument("tex_file", type=Path)
    compile_tex.add_argument("--attempt", type=int, default=1)
    compile_remote = commands.add_parser("compile-remote", help="Скомпилировать TEX в ветке занятия")
    compile_remote.add_argument("lesson_json", type=Path)
    compile_remote.add_argument("--force", action="store_true")
    commands.add_parser("scan-latex", help="Найти занятия с новым TEX в удалённых ветках")
    return root


def main() -> None:
    args = parser().parse_args()
    config = AppConfig.load(args.config)
    if args.command == "devices":
        inputs = list_input_devices()
        system_sources = list_system_audio_sources(inputs, config.recording.target_sample_rate)
        print(
            json.dumps(
                {
                    "microphones": [device.to_dict() for device in inputs],
                    "system_audio": [source.to_dict() for source in system_sources],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "doctor":
        from .diagnostics import format_diagnostics, report_json, run_diagnostics

        report = run_diagnostics(config, args.config)
        print(report_json(report) if args.json else format_diagnostics(report))
        if args.strict and not report.ready:
            raise SystemExit(1)
        return
    if args.command == "latex-doctor":
        from .latex import inspect_latex_environment

        print(json.dumps(inspect_latex_environment(config.latex).to_dict(), ensure_ascii=False, indent=2))
        return
    if args.command == "compile":
        from .latex import LatexCompiler

        result = LatexCompiler(config.latex).compile(args.tex_file, attempt=args.attempt)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        raise SystemExit(0 if result.success else 1)
    pipeline = LessonPipeline(config)
    if args.command == "create":
        students = {item.id: item for item in load_students(config.students_file)}
        lesson = Lesson(
            student=students[args.student],
            subject=args.subject,
            lesson_date=date.fromisoformat(args.date),
            topic=args.topic,
        )
        print(pipeline.create(lesson) / "lesson.json")
    elif args.command == "transcribe":
        lesson = Lesson.read_json(args.lesson_json)
        pipeline.transcribe(lesson, args.audio)
        print(pipeline.lesson_dir(lesson) / "lesson.json")
    elif args.command == "publish":
        lesson = Lesson.read_json(args.lesson_json)
        print(pipeline.publish(lesson))
    elif args.command == "compile-remote":
        from .latex import RemoteLatexService

        lesson = Lesson.read_json(args.lesson_json)
        result = RemoteLatexService(config.repository, config.latex).compile_lesson(
            lesson,
            force=args.force,
            cache_dir=pipeline.lesson_dir(lesson) / "latex-cache",
        )
        result.lesson.write_json(args.lesson_json)
        pipeline.store.save(result.lesson)
        print(
            json.dumps(
                {
                    "success": result.compilation.success,
                    "branch": result.branch,
                    "commit": result.commit,
                    "pdf": result.lesson.latex.pdf_path,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "scan-latex":
        from .latex import RemoteLatexService

        service = RemoteLatexService(config.repository, config.latex)
        ready = []
        for lesson in pipeline.store.list():
            try:
                if service.is_ready(lesson):
                    ready.append(
                        {
                            "lesson_id": lesson.lesson_id,
                            "student": lesson.student.full_name,
                            "topic": lesson.topic,
                            "branch": lesson.publication.branch,
                        }
                    )
            except Exception as exc:
                ready.append({"lesson_id": lesson.lesson_id, "error": str(exc)})
        print(json.dumps(ready, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
