from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from ..config import LatexConfig
from .diagnostics import inspect_latex_environment
from .models import CompilationResult
from .validator import validate_tex

ERROR_LINE = re.compile(r"(?:^|\n)([^\n:]+\.tex):(\d+):\s*(.+)")
LATEX_ERROR = re.compile(r"(?:^|\n)!\s*(.+)")


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _run_with_timeout(
    command: list[str], cwd: Path, timeout: int, environment: dict[str, str] | None = None
) -> tuple[int, str, bool]:
    kwargs: dict = {
        "cwd": cwd, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
        "text": True, "encoding": "utf-8", "errors": "replace",
        "env": environment,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    try:
        output, _ = process.communicate(timeout=timeout)
        return process.returncode, output, False
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        output, _ = process.communicate()
        return -1, output, True


def _parse_errors(log: str) -> list[str]:
    errors: list[str] = []
    for filename, line, message in ERROR_LINE.findall(log):
        errors.append(f"{Path(filename).name}:{line}: {message.strip()}")
    for message in LATEX_ERROR.findall(log):
        cleaned = message.strip()
        if cleaned and cleaned not in errors:
            errors.append(cleaned)
    if not errors and "Latexmk: Errors" in log:
        errors.append("latexmk сообщил об ошибке; подробности сохранены в compilation.log")
    return errors[:50]


def _inspect_pdf(pdf: Path) -> tuple[int, int, list[str]]:
    warnings: list[str] = []
    size = pdf.stat().st_size
    if size == 0:
        return 0, 0, ["Создан пустой PDF"]
    if size > 50 * 1024 * 1024:
        warnings.append("PDF превышает 50 МБ")
    try:
        from pypdf import PdfReader

        reader = PdfReader(pdf)
        pages = len(reader.pages)
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            resources = page.get("/Resources") or {}
            has_xobject = bool(resources.get("/XObject")) if hasattr(resources, "get") else False
            if not text and not has_xobject:
                warnings.append(f"Страница {index} может быть пустой")
        return pages, size, warnings
    except Exception as exc:
        warnings.append(f"Структура PDF не проверена: {exc}")
        return 0, size, warnings


def _render_preview(pdf: Path, destination: Path, dpi: int) -> tuple[list[Path], list[str]]:
    command = shutil.which("pdftoppm")
    if not command:
        return [], ["pdftoppm не найден: предпросмотр пропущен"]
    destination.mkdir(parents=True, exist_ok=True)
    prefix = destination / "page"
    result = subprocess.run(
        [command, "-png", "-r", str(dpi), str(pdf), str(prefix)],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode:
        return [], ["Не удалось отрисовать PNG-предпросмотр: " + result.stderr.strip()]
    return sorted(destination.glob("page-*.png")), []


def _fix_request(tex_file: Path, attempt: int, max_attempts: int, errors: list[str], log: str) -> str:
    error_block = "\n".join(f"- {item}" for item in errors) or "- Неопознанная ошибка компиляции"
    return f"""# Запрос на исправление LaTeX

Файл: `{tex_file.name}`  
Попытка: `{attempt}/{max_attempts}`

Исправь только ошибки, блокирующие компиляцию пособия. Сохрани учебное содержание, упражнения,
ответы, оформление и данные ученика. Не используй `shell-escape`, `write18`, абсолютные пути и
чтение файлов вне каталога занятия. После исправления замени исходный `.tex` и оставь статус
задания `generated_tex`, чтобы локальный компилятор выполнил следующую попытку.

## Выделенные ошибки

{error_block}

## Конец журнала

```text
{log[-8000:]}
```
"""


class LatexCompiler:
    def __init__(self, config: LatexConfig) -> None:
        self.config = config

    def _command(self, tex_name: str, output_dir: Path) -> list[str]:
        engine = self.config.engine.lower()
        if engine not in {"pdflatex", "xelatex", "lualatex"}:
            raise ValueError(f"Неподдерживаемый LaTeX-движок: {engine}")
        mode = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}[engine]
        engine_option = f"-{engine}={engine} -no-shell-escape -recorder %O %S"
        return [
            self.config.latexmk_command, mode, "-interaction=nonstopmode", "-halt-on-error",
            "-file-line-error", f"-outdir={output_dir}", engine_option, tex_name,
        ]

    def compile(
        self,
        tex_file: Path,
        *,
        attempt: int = 1,
        report_dir: Path | None = None,
        preview_dir: Path | None = None,
    ) -> CompilationResult:
        tex_file = tex_file.resolve()
        if not tex_file.is_file():
            raise FileNotFoundError(tex_file)
        report_dir = report_dir or tex_file.parent / "build"
        preview_dir = preview_dir or tex_file.parent / "preview"
        report_dir.mkdir(parents=True, exist_ok=True)
        log_file = report_dir / "compilation.log"
        report_file = report_dir / "compilation.json"
        issues = validate_tex(tex_file)
        started = perf_counter()
        if issues:
            log = "Компиляция заблокирована проверкой безопасности.\n" + "\n".join(
                f"{issue.line or '-'}: {issue.message}" for issue in issues
            )
            log_file.write_text(log, encoding="utf-8")
            result = CompilationResult(
                False, tex_file, None, log_file, report_file, perf_counter() - started,
                errors=[issue.message for issue in issues], validation_issues=issues,
            )
            self._finish(result, attempt, log)
            return result

        environment = inspect_latex_environment(self.config)
        if not environment.latexmk or not environment.engine:
            log = "\n".join(environment.messages)
            log_file.write_text(log, encoding="utf-8")
            result = CompilationResult(
                False, tex_file, None, log_file, report_file, perf_counter() - started,
                errors=environment.messages,
            )
            self._finish(result, attempt, log)
            return result

        with tempfile.TemporaryDirectory(prefix="tutor-latex-") as raw:
            working = Path(raw) / "source"
            shutil.copytree(
                tex_file.parent, working,
                ignore=shutil.ignore_patterns("build", "preview", "reports", ".git"),
            )
            build = working / ".tutor-build"
            build.mkdir()
            command = self._command(tex_file.name, Path(".tutor-build"))
            environment = os.environ.copy()
            environment.update({
                "openin_any": "p",
                "openout_any": "p",
                "TEXMFOUTPUT": str(build),
            })
            returncode, log, timed_out = _run_with_timeout(
                command, working, self.config.timeout_seconds, environment
            )
            log_file.write_text(log, encoding="utf-8")
            built_pdf = build / tex_file.with_suffix(".pdf").name
            errors = _parse_errors(log)
            if timed_out:
                errors.insert(0, f"Компиляция превысила {self.config.timeout_seconds} секунд")
            success = returncode == 0 and built_pdf.is_file() and built_pdf.stat().st_size > 0
            output_pdf = tex_file.with_suffix(".pdf") if success else None
            warnings: list[str] = []
            pages = size = 0
            previews: list[Path] = []
            if success:
                shutil.copy2(built_pdf, output_pdf)
                pages, size, pdf_warnings = _inspect_pdf(output_pdf)
                warnings.extend(pdf_warnings)
                if self.config.render_preview:
                    if preview_dir.exists():
                        shutil.rmtree(preview_dir)
                    previews, render_warnings = _render_preview(
                        output_pdf, preview_dir, self.config.preview_dpi
                    )
                    warnings.extend(render_warnings)
            result = CompilationResult(
                success, tex_file, output_pdf, log_file, report_file,
                round(perf_counter() - started, 3), pages, size, warnings, errors,
                timed_out=timed_out, preview_files=previews,
            )
            self._finish(result, attempt, log)
            return result

    def _finish(self, result: CompilationResult, attempt: int, log: str) -> None:
        if not result.success and attempt < self.config.max_attempts:
            request = result.report_file.parent / "latex_fix_request.md"
            request.write_text(
                _fix_request(result.tex_file, attempt, self.config.max_attempts, result.errors, log),
                encoding="utf-8",
            )
            result.fix_request_file = request
        payload = result.to_dict()
        payload.update({
            "attempt": attempt,
            "max_attempts": self.config.max_attempts,
            "engine": self.config.engine,
            "shell_escape": False,
            "created_at": datetime.now(UTC).isoformat(),
        })
        result.report_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
