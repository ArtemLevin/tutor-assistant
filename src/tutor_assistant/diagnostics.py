from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import AppConfig


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

    version = sys.version_info
    supported_python = (3, 11) <= version[:2] < (3, 15)
    checks.append(
        _check(
            "Python",
            supported_python,
            f"{version.major}.{version.minor}.{version.micro}",
            f"{version.major}.{version.minor}.{version.micro}; требуется Python 3.11–3.14",
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
        ("sounddevice", "SoundDevice"),
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

    gh_required = config.repository.push and config.repository.auto_create_pr
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
