from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .config import AppConfig, load_students
from .domain import Lesson
from .logging_config import configure_logging, install_exception_hook
from .paths import default_config_path
from .pipeline import LessonPipeline
from .recording import list_input_devices, list_system_audio_sources


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tutor-assistant")
    from . import __version__

    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--config", type=Path, default=default_config_path())
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("devices", help="Показать входные аудиоустройства")
    support = commands.add_parser("support-bundle", help="Собрать безопасный ZIP диагностики")
    support.add_argument("--output", type=Path)
    doctor = commands.add_parser("doctor", help="Проверить всё окружение приложения")
    doctor.add_argument("--json", action="store_true", help="Вывести машиночитаемый JSON")
    doctor.add_argument("--strict", action="store_true", help="Вернуть код 1 при обязательных ошибках")
    recovery_drill = commands.add_parser(
        "recovery-drill",
        help="Проверить восстановление в изолированной песочнице без изменения рабочего каталога",
    )
    recovery_drill.add_argument("--output", type=Path, help="Сохранить JSON-отчёт вне рабочего каталога")
    hardware_soak = commands.add_parser(
        "hardware-soak",
        help="Собрать privacy-safe отчёт и оценить физические сценарии проверки записи",
    )
    hardware_soak.add_argument("--output", type=Path)
    hardware_soak.add_argument(
        "--evidence",
        type=Path,
        help="JSON-файл ранее собранных аппаратных наблюдений",
    )
    hardware_soak.add_argument("--strict", action="store_true")
    commands.add_parser(
        "content-index",
        help="Проиндексировать существующие локальные занятия, аудио и транскрипты",
    )
    content_doctor = commands.add_parser(
        "content-doctor",
        help="Проверить SQLite, поиск и локальное хранилище материалов",
    )
    content_doctor.add_argument("--json", action="store_true")
    content_doctor.add_argument("--cleanup-temp", action="store_true")
    content_doctor.add_argument("--rebuild-search", action="store_true")
    content_doctor.add_argument("--repair", action="store_true")
    content_doctor.add_argument("--import-legacy", action="store_true")
    content_doctor.add_argument("--purge-expired", action="store_true")
    content_doctor.add_argument("--strict", action="store_true")
    content_doctor.add_argument("--mode", choices=("quick", "full"), default="full")
    content_doctor.add_argument("--max-lessons", type=int)
    content_doctor.add_argument("--max-seconds", type=int)
    content_backup = commands.add_parser(
        "content-backup",
        help="Создать, проверить или восстановить резервную копию SQLite",
    )
    backup_action = content_backup.add_mutually_exclusive_group()
    backup_action.add_argument("--create", action="store_true")
    backup_action.add_argument("--verify", type=Path)
    backup_action.add_argument("--restore", type=Path)
    backup_action.add_argument("--prune", action="store_true")
    content_backup.add_argument(
        "--reason",
        choices=("manual", "pre-upgrade"),
        default="manual",
        help="Класс создаваемой резервной копии; pre-upgrade сохраняется вне scheduled retention",
    )
    content_backup.add_argument(
        "--yes",
        action="store_true",
        help="Подтвердить восстановление основной базы данных",
    )
    create = commands.add_parser("create", help="Создать занятие")
    create.add_argument("--student", required=True)
    create.add_argument("--subject", required=True)
    create.add_argument("--topic", required=True)
    create.add_argument("--date", default=date.today().isoformat())
    transcribe = commands.add_parser("transcribe", help="Транскрибировать аудио")
    transcribe.add_argument("lesson_json", type=Path)
    transcribe.add_argument("audio", type=Path)
    def add_filter_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("lesson_id")
        command.add_argument("--provider", choices=("ollama", "yandex_ai_studio"))
        command.add_argument("--model")
        command.add_argument("--force", action="store_true")
        command.add_argument(
            "--allow-cloud",
            action="store_true",
            help="Явно разрешить передачу текущего транскрипта в облако",
        )
        command.add_argument("--dry-run", action="store_true")
        command.add_argument("--output", type=Path)
        command.add_argument(
            "--retry-indeterminate",
            action="store_true",
            help="Явно повторить неопределённый облачный запрос после аварийного завершения",
        )
        command.add_argument(
            "--no-apply",
            action="store_true",
            help="Результат всегда требует ручного применения",
        )
        command.add_argument(
            "--include-removed-text",
            action="store_true",
            default=None,
            help=argparse.SUPPRESS,
        )

    content_filter = commands.add_parser(
        "filter-transcript",
        help="Отфильтровать учебное содержание через Ollama или Yandex AI Studio",
    )
    add_filter_arguments(content_filter)
    normalize = commands.add_parser(
        "normalize",
        help="Совместимый алиас команды filter-transcript",
    )
    add_filter_arguments(normalize)

    def add_filter_doctor_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--provider", choices=("ollama", "yandex_ai_studio"))
        command.add_argument("--model")
        command.add_argument("--json", action="store_true")

    content_filter_doctor = commands.add_parser(
        "content-filter-doctor",
        help="Проверить LLM-фильтр на синтетическом учебном тексте",
    )
    add_filter_doctor_arguments(content_filter_doctor)
    normalization_doctor = commands.add_parser(
        "normalization-doctor",
        help="Совместимый алиас команды content-filter-doctor",
    )
    add_filter_doctor_arguments(normalization_doctor)
    privacy = commands.add_parser(
        "privacy-doctor",
        help="Проверить privacy boundary, credentials и локальный аудит облака",
    )
    privacy.add_argument("--json", action="store_true")
    privacy.add_argument("--strict", action="store_true")
    credentials = commands.add_parser(
        "credentials",
        help="Управление безопасными credentials",
    )
    credentials.add_argument("credential_provider", choices=("yandex",))
    credentials.add_argument("credential_action", choices=("status", "set", "delete"))
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
    if args.command != "recovery-drill":
        configure_logging(config.workspace)
        install_exception_hook(config.workspace)
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
    if args.command == "support-bundle":
        from .support import create_support_bundle

        print(create_support_bundle(config, args.config, args.output))
        return
    if args.command == "doctor":
        from .diagnostics import format_diagnostics, report_json, run_diagnostics

        report = run_diagnostics(config, args.config)
        print(report_json(report) if args.json else format_diagnostics(report))
        if args.strict and not report.ready:
            raise SystemExit(1)
        return
    if args.command == "recovery-drill":
        from .recovery_drill import run_recovery_drill, write_recovery_drill_report

        report = run_recovery_drill(config.workspace)
        if args.output:
            write_recovery_drill_report(report, args.output, live_workspace=config.workspace)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        if not report.passed:
            raise SystemExit(1)
        return
    if args.command == "hardware-soak":
        from .hardware_soak import (
            collect_hardware_observations,
            evaluate_hardware_soak,
            write_hardware_soak_report,
        )

        observations = (
            json.loads(args.evidence.read_text(encoding="utf-8"))
            if args.evidence
            else collect_hardware_observations(config.workspace)
        )
        report = evaluate_hardware_soak(observations)
        if args.output:
            write_hardware_soak_report(report, args.output)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        if args.strict and not report.passed:
            raise SystemExit(1)
        return
    if args.command == "privacy-doctor":
        from .security.privacy_diagnostics import (
            format_privacy_report,
            run_privacy_diagnostics,
        )

        report = run_privacy_diagnostics(config, args.config)
        print(report.model_dump_json(indent=2) if args.json else format_privacy_report(report))
        if args.strict and not report.ready:
            raise SystemExit(1)
        return
    if args.command == "credentials":
        from .security.credentials import (
            credential_status,
            delete_yandex_api_key,
            save_yandex_api_key,
        )

        if args.credential_action == "status":
            status = credential_status(config.normalization)
            print(
                json.dumps(
                    {
                        "configured": status.configured,
                        "source": status.source,
                        "detail": status.detail,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if args.credential_action == "set":
            value = getpass.getpass("Yandex AI Studio API key: ")
            save_yandex_api_key(config.normalization, value)
            print("API-ключ сохранён в Windows Credential Manager.")
            return
        delete_yandex_api_key(config.normalization)
        print("Сохранённый API-ключ удалён.")
        return
    if args.command == "latex-doctor":
        from .latex import inspect_latex_environment

        print(json.dumps(inspect_latex_environment(config.latex).to_dict(), ensure_ascii=False, indent=2))
        return
    if args.command in {"content-filter-doctor", "normalization-doctor"}:
        from .config import NormalizationConfig
        from .normalization import build_provider

        normalization_config = config.normalization
        if args.provider:
            normalization_config = NormalizationConfig.model_validate(
                {**normalization_config.model_dump(), "provider": args.provider}
            )
        report = build_provider(
            normalization_config,
            model=args.model or normalization_config.effective_model,
        ).diagnose()
        if args.json:
            print(report.model_dump_json(indent=2))
        else:
            status = "ГОТОВ" if report.plain_text_valid else "ТРЕБУЕТ НАСТРОЙКИ"
            print(
                f"LLM-фильтр {report.provider}: {status}\n"
                f"Endpoint: {report.endpoint}\n"
                f"Endpoint локальный: {'да' if report.endpoint_local else 'нет'}\n"
                f"Версия: {report.version or '—'}\n"
                f"Модель доступна: {'да' if report.model_available else 'нет'}\n"
                f"Plain text: {'да' if report.plain_text_valid else 'нет'}"
            )
            for error in report.errors:
                print(f"- {error}")
        raise SystemExit(0 if report.plain_text_valid else 1)
    if args.command == "content-index":
        from .content import StudentContentService

        report = StudentContentService(config.workspace).index_existing_lessons()
        print(report.model_dump_json(indent=2))
        raise SystemExit(1 if report.errors else 0)
    if args.command == "content-doctor":
        from .content import IntegrityCheckMode, StudentContentService

        mode = IntegrityCheckMode(args.mode)
        service = StudentContentService(
            config.workspace,
            trash_retention_days=config.content.trash_retention_days,
        )
        legacy_import = service.repair_archive() if args.import_legacy else None
        maintenance = None
        if args.repair or args.cleanup_temp or args.purge_expired:
            maintenance = service.run_maintenance(
                auto_repair=args.repair,
                purge_expired=args.purge_expired,
                cleanup_temporary=args.cleanup_temp,
                temporary_retention=timedelta(hours=config.content.temporary_retention_hours),
                mode=mode,
                max_lessons=args.max_lessons or config.content.maintenance_max_lessons_per_cycle,
                max_seconds=args.max_seconds or config.content.maintenance_max_seconds,
                apply_max_seconds=config.content.maintenance_apply_max_seconds,
            )
        cleanup = maintenance.temporary_cleanup if maintenance and args.cleanup_temp else None
        rebuilt = service.coordinated_rebuild_search_index() if args.rebuild_search else None
        report = service.inspect_content_integrity(mode=mode)
        if args.json:
            payload = report.model_dump(mode="json")
            payload.update(
                {
                    "healthy": report.healthy,
                    "errors": report.errors,
                    "warnings": report.warnings,
                    "cleanup": cleanup.model_dump(mode="json") if cleanup else None,
                    "rebuilt_search_documents": rebuilt,
                    "maintenance": maintenance.model_dump(mode="json") if maintenance else None,
                    "legacy_import": legacy_import.model_dump(mode="json") if legacy_import else None,
                }
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"SQLite: {report.database_message}; "
                f"поиск: {'FTS5' if report.fts_enabled else 'fallback'} "
                f"({report.fts_documents}); ошибок: {report.errors}; "
                f"предупреждений: {report.warnings}"
            )
            for issue in report.issues:
                location = issue.relative_path or issue.lesson_id or "—"
                print(f"[{issue.severity.value.upper()}] {issue.code} · {location} · {issue.message}")
            if cleanup:
                print(
                    f"Временные данные: удалено {len(cleanup.removed_paths)}, "
                    f"освобождено {cleanup.released_bytes} байт, ошибок {len(cleanup.errors)}"
                )
            if rebuilt is not None:
                print(f"FTS-документов перестроено: {rebuilt}")
            if maintenance:
                print(
                    f"Обслуживание: восстановлено {len(maintenance.repaired_lessons)}, "
                    f"очищено из корзины {len(maintenance.purged_lessons)}, "
                    f"ошибок {len(maintenance.errors)}"
                )
            if legacy_import:
                print(
                    f"Legacy-каталоги: занятий {legacy_import.indexed_lessons}, "
                    f"файлов {legacy_import.indexed_assets}, ошибок {len(legacy_import.errors)}"
                )
        if args.strict and (not report.healthy or bool(maintenance and maintenance.errors)):
            raise SystemExit(1)
        return
    if args.command == "content-backup":
        from .content import StudentContentService

        if args.restore and not args.yes:
            raise SystemExit("Для восстановления укажите --yes")
        try:
            service = StudentContentService(
                config.workspace,
                trash_retention_days=config.content.trash_retention_days,
            )
        except sqlite3.DatabaseError:
            if not args.restore:
                raise
            payload = StudentContentService.restore_database_backup_offline(
                config.workspace,
                args.restore,
            )
            recovered = StudentContentService(
                config.workspace,
                trash_retention_days=config.content.trash_retention_days,
            )
            recovered.repair_content_integrity()
            print(payload.model_dump_json(indent=2))
            return
        if args.create:
            payload: object = service.create_database_backup(reason=args.reason)
        elif args.verify:
            payload = service.verify_database_backup(args.verify)
        elif args.restore:
            payload = service.restore_database_backup(args.restore)
        elif args.prune:
            payload = service.prune_database_backups(config.content.backup_retention_count)
        else:
            payload = service.list_database_backups()
        if isinstance(payload, list):
            print(
                json.dumps(
                    [item.model_dump(mode="json") for item in payload],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(payload.model_dump_json(indent=2))
        if args.verify and not payload.valid:
            raise SystemExit(1)
        return
    if args.command == "compile":
        from .latex import LatexCompiler

        result = LatexCompiler(config.latex).compile(args.tex_file, attempt=args.attempt)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        raise SystemExit(0 if result.success else 1)
    pipeline = LessonPipeline(config)
    if args.command in {"filter-transcript", "normalize"}:
        from .config import NormalizationConfig
        from .content import ContentBusyError, ContentNotFoundError
        from .normalization import NormalizationError, NormalizationService
        from .security.cloud_consent import CloudConsentReceipt

        normalization_config = config.normalization
        if args.provider:
            normalization_config = NormalizationConfig.model_validate(
                {**normalization_config.model_dump(), "provider": args.provider}
            )
        service = NormalizationService(normalization_config, pipeline.content_service)
        cloud_consent = None
        if normalization_config.provider == "yandex_ai_studio":
            if not args.allow_cloud:
                raise SystemExit(
                    "Для Yandex AI Studio укажите --allow-cloud после проверки объёма данных"
                )
            request = service.cloud_processing_request(
                args.lesson_id,
                model=args.model,
            )
            cloud_consent = CloudConsentReceipt.grant(request)
        try:
            result = service.normalize_lesson(
                args.lesson_id,
                model=args.model,
                force=args.force,
                dry_run=args.dry_run,
                output=args.output,
                include_removed_text=args.include_removed_text,
                retry_indeterminate=args.retry_indeterminate,
                cloud_consent=cloud_consent,
            )
        except (NormalizationError, ContentBusyError, ContentNotFoundError) as exc:
            raise SystemExit(str(exc)) from None
        print(result.transcript.educational_text)
        return
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
        result = pipeline.compile_remote_latex(
            lesson,
            force=args.force,
            cache_dir=pipeline.lesson_dir(lesson) / "latex-cache",
        )
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
        for lesson in pipeline.content_service.iter_lessons():
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
