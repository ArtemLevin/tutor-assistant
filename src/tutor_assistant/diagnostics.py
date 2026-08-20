from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import AppConfig
from .runtime import inspect_runtime


@dataclass(frozen=True)
class DiagnosticCheck:
    name: str
    status: str
    message: str
    required: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DiagnosticReport:
    ready: bool
    checks: tuple[DiagnosticCheck, ...]

    @property
    def errors(self) -> int:
        return sum(check.required and check.status == "error" for check in self.checks)

    @property
    def warnings(self) -> int:
        return sum(check.status == "warning" for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "errors": self.errors,
            "warnings": self.warnings,
            "checks": [check.to_dict() for check in self.checks],
        }


def _check(name: str, ok: bool, success: str, failure: str, *, required: bool = True) -> DiagnosticCheck:
    return DiagnosticCheck(
        name=name,
        status="ok" if ok else ("error" if required else "warning"),
        message=success if ok else failure,
        required=required,
    )


def _module_check(module: str, label: str, *, required: bool = True) -> DiagnosticCheck:
    available = importlib.util.find_spec(module) is not None
    return _check(
        label,
        available,
        f"Модуль {module} доступен",
        f"Модуль {module} отсутствует; выполните uv sync --all-extras",
        required=required,
    )


def _command_check(command: str, label: str, *, required: bool = True) -> DiagnosticCheck:
    executable = shutil.which(command)
    return _check(
        label,
        executable is not None,
        executable or command,
        f"Команда {command} отсутствует в PATH",
        required=required,
    )


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _run(command: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def run_diagnostics(config: AppConfig, config_path: Path = Path("config/app.yaml")) -> DiagnosticReport:
    checks: list[DiagnosticCheck] = []

    runtime = inspect_runtime()
    checks.append(
        _check(
            "Python",
            runtime.supported,
            runtime.version,
            f"{runtime.version}; требуется Python 3.12–3.14",
        )
    )
    checks.append(
        DiagnosticCheck(
            "Production runtime",
            "ok" if runtime.production else "warning",
            "YES (Python 3.12)" if runtime.production else "NO; packaged releases require Python 3.12",
            required=False,
        )
    )
    checks.append(
        DiagnosticCheck(
            "Compatibility runtime",
            "ok" if runtime.supported else "error",
            "YES" if runtime.compatibility else "NO" if runtime.production else "UNSUPPORTED",
            required=not runtime.supported,
        )
    )
    checks.append(_command_check("uv", "uv"))

    config_exists = config_path.exists()
    checks.append(
        _check(
            "Конфигурация",
            config_exists and config.setup_completed,
            str(config_path.resolve()),
            (
                f"{config_path} ещё не настроен; выполните make setup"
                if config_exists
                else f"{config_path} отсутствует; выполните make init или make setup"
            ),
        )
    )

    workspace_parent = _nearest_existing_parent(config.workspace)
    workspace_writable = workspace_parent.is_dir() and os.access(workspace_parent, os.W_OK)
    checks.append(
        _check(
            "Рабочий каталог",
            workspace_writable,
            f"{config.workspace} (доступен для записи)",
            f"Невозможно записывать в {config.workspace}",
        )
    )
    try:
        free_bytes = shutil.disk_usage(workspace_parent).free
        checks.append(
            _check(
                "Свободное место",
                free_bytes >= 1024**3,
                f"{free_bytes / 1024**3:.1f} ГБ доступно",
                f"Осталось {free_bytes / 1024**3:.1f} ГБ; рекомендуется освободить место",
                required=False,
            )
        )
    except OSError as exc:
        checks.append(DiagnosticCheck("Свободное место", "warning", str(exc), required=False))
    checks.append(
        _check(
            "Список учеников",
            config.students_file.is_file(),
            str(config.students_file),
            f"Файл {config.students_file} отсутствует",
        )
    )

    for module, label in (
        ("PySide6", "Desktop UI"),
        ("faster_whisper", "Whisper"),
        ("httpx", "LLM HTTP"),
        ("sounddevice", "SoundDevice"),
        ("soundcard", "WASAPI Loopback"),
        ("soundfile", "SoundFile"),
        ("pypdf", "PDF"),
    ):
        checks.append(_module_check(module, label))

    checks.append(_command_check("git", "Git"))
    checks.append(_command_check("ffmpeg", "FFmpeg", required=False))
    checks.append(_command_check("ffprobe", "FFprobe", required=False))

    students_repo = config.repository.students_repo.expanduser()
    checks.append(
        _check(
            "Репозиторий учеников",
            (students_repo / ".git").exists(),
            str(students_repo.resolve()),
            f"Git-репозиторий не найден: {students_repo}",
        )
    )

    gh_required = False
    gh_path = shutil.which("gh")
    checks.append(
        _check(
            "GitHub CLI",
            gh_path is not None,
            gh_path or "gh",
            "Команда gh отсутствует в PATH",
            required=gh_required,
        )
    )
    if gh_path:
        auth = _run([gh_path, "auth", "status"], timeout=15)
        authenticated = auth is not None and auth.returncode == 0
        details = "GitHub CLI авторизован"
        if not authenticated:
            details = "GitHub CLI требует авторизации: выполните gh auth login"
        checks.append(
            DiagnosticCheck(
                name="GitHub авторизация",
                status="ok" if authenticated else ("error" if gh_required else "warning"),
                message=details,
                required=gh_required,
            )
        )

    try:
        from .recording import list_input_devices

        devices = list_input_devices()
        checks.append(
            _check(
                "Аудиоустройства",
                bool(devices),
                f"Найдено входных устройств: {len(devices)}",
                "Входные аудиоустройства не найдены",
            )
        )
    except Exception as exc:
        checks.append(DiagnosticCheck("Аудиоустройства", "error", str(exc), required=True))

    try:
        from .recording import list_loopback_devices

        system_sources = list_loopback_devices(config.recording.target_sample_rate)
        checks.append(
            _check(
                "WASAPI Loopback",
                bool(system_sources),
                f"Найдено loopback-устройств: {len(system_sources)}",
                "WASAPI Loopback-устройства не найдены",
            )
        )
    except Exception as exc:
        checks.append(DiagnosticCheck("WASAPI Loopback", "error", str(exc), required=True))

    if config.latex.enabled:
        try:
            from .latex import inspect_latex_environment

            latex = inspect_latex_environment(config.latex)
            checks.append(
                _check(
                    "LaTeX",
                    latex.ready,
                    f"{config.latex.engine} и {config.latex.latexmk_command} готовы",
                    "; ".join(latex.messages) or "LaTeX-окружение не готово",
                )
            )
            if config.latex.render_preview:
                checks.append(
                    _check(
                        "PDF-предпросмотр",
                        bool(latex.pdftoppm),
                        latex.pdftoppm or "pdftoppm",
                        "pdftoppm отсутствует; PNG-предпросмотр недоступен",
                        required=False,
                    )
                )
        except Exception as exc:
            checks.append(DiagnosticCheck("LaTeX", "error", str(exc), required=True))
    else:
        checks.append(DiagnosticCheck("LaTeX", "ok", "Автокомпиляция отключена", required=False))

    normalization_directory = config.workspace / "lessons"
    normalization_parent = _nearest_existing_parent(normalization_directory)
    checks.append(
        _check(
            "Артефакты нормализации",
            normalization_parent.is_dir() and os.access(normalization_parent, os.W_OK),
            f"{normalization_directory} (доступен для записи)",
            f"Невозможно сохранять текст в {normalization_directory}",
            required=config.normalization.enabled,
        )
    )
    if config.normalization.enabled:
        try:
            from .normalization import build_provider

            diagnostics = build_provider(config.normalization).diagnose()
            endpoint_ready = diagnostics.reachable and (
                diagnostics.endpoint_local
                or config.normalization.provider == "yandex_ai_studio"
                or config.normalization.allow_remote_endpoint
            )
            endpoint_status = "ok" if endpoint_ready else "error"
            endpoint_required = True
            endpoint_message = f"{diagnostics.endpoint}; версия {diagnostics.version or '—'}"
            if diagnostics.reachable and not diagnostics.endpoint_local:
                endpoint_status = "warning"
                endpoint_required = False
                endpoint_message = (
                    f"{diagnostics.endpoint}; используется облачная обработка, "
                    "транскрипт покинет этот компьютер"
                )
            elif not endpoint_ready:
                endpoint_message = (
                    "; ".join(diagnostics.errors) or f"Endpoint недоступен: {diagnostics.endpoint}"
                )
            checks.extend(
                (
                    DiagnosticCheck(
                        "Normalization endpoint",
                        endpoint_status,
                        endpoint_message,
                        required=endpoint_required,
                    ),
                    _check(
                        "Normalization model",
                        diagnostics.model_available,
                        config.normalization.effective_model,
                        f"Модель {config.normalization.effective_model} недоступна",
                    ),
                    _check(
                        "Normalization plain text",
                        diagnostics.plain_text_valid,
                        "Синтетический plain-text ответ прошёл проверку",
                        "Provider не вернул валидный тестовый текст",
                    ),
                )
            )
        except Exception as exc:
            checks.append(
                DiagnosticCheck(
                    "Нормализация",
                    "error",
                    str(exc),
                    required=True,
                )
            )
    else:
        checks.append(
            DiagnosticCheck(
                "Нормализация",
                "ok",
                "LLM-фильтрация отключена",
                required=False,
            )
        )

    if config.content.backup_enabled:
        status_path = config.workspace / "maintenance" / "backup-status.json"
        try:
            backup_status = json.loads(status_path.read_text(encoding="utf-8"))
            verified = bool(backup_status.get("verified"))
            last_error = backup_status.get("last_error")
            message = (
                f"последняя: {backup_status.get('last_successful_at') or '—'}; "
                f"следующая: {backup_status.get('next_due_at') or '—'}; "
                f"copies: {backup_status.get('scheduled_copy_count', 0)}"
            )
            if last_error:
                message += f"; ошибка: {last_error}"
            checks.append(
                DiagnosticCheck(
                    "Automatic backup",
                    "ok" if verified and not last_error else "warning",
                    message,
                    required=False,
                )
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            checks.append(
                DiagnosticCheck(
                    "Automatic backup",
                    "warning",
                    "Проверенная плановая резервная копия пока не создана",
                    required=False,
                )
            )
    else:
        checks.append(DiagnosticCheck("Automatic backup", "ok", "Отключено пользователем", required=False))

    ready = not any(check.required and check.status == "error" for check in checks)
    return DiagnosticReport(ready=ready, checks=tuple(checks))


def format_diagnostics(report: DiagnosticReport) -> str:
    markers = {"ok": "[OK]", "warning": "[WARN]", "error": "[FAIL]"}
    width = max((len(check.name) for check in report.checks), default=0)
    lines = [f"{markers[check.status]} {check.name:<{width}}  {check.message}" for check in report.checks]
    result = "ГОТОВО" if report.ready else "ТРЕБУЕТ НАСТРОЙКИ"
    lines.extend(("", f"Итог: {result}; ошибок: {report.errors}; предупреждений: {report.warnings}"))
    return "\n".join(lines)


def report_json(report: DiagnosticReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
