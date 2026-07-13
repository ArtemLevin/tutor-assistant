from __future__ import annotations

import re
from pathlib import Path

from .models import ValidationIssue

FORBIDDEN_PATTERNS = {
    "shell-command": re.compile(r"\\(?:immediate\s*)?write18\b", re.I),
    "shell-package": re.compile(r"\\usepackage(?:\[[^]]*])?\{(?:shellesc|catchfile)\}", re.I),
    "file-read": re.compile(r"\\openin\b", re.I),
    "file-write": re.compile(r"\\openout\b", re.I),
    "pipe-input": re.compile(r"\\input\s*\{?\s*\|", re.I),
}

PATH_COMMAND = re.compile(
    r"\\(?:input|include|includegraphics|addbibresource|bibliography)"
    r"(?:\[[^]]*])?\{([^}]+)\}",
    re.I,
)


def _without_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        lines.append(re.split(r"(?<!\\)%", line, maxsplit=1)[0])
    return "\n".join(lines)


def _line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def validate_tex(tex_file: Path) -> list[ValidationIssue]:
    text = _without_comments(tex_file.read_text(encoding="utf-8"))
    issues: list[ValidationIssue] = []
    for code, pattern in FORBIDDEN_PATTERNS.items():
        for match in pattern.finditer(text):
            issues.append(
                ValidationIssue(
                    code, f"Запрещённая команда LaTeX: {match.group(0)}", _line_number(text, match.start())
                )
            )
    for match in PATH_COMMAND.finditer(text):
        raw_targets = [item.strip() for item in match.group(1).split(",")]
        for target in raw_targets:
            normalized = target.replace("\\", "/")
            windows_absolute = bool(re.match(r"^[a-zA-Z]:/", normalized))
            if normalized.startswith("/") or windows_absolute or ".." in Path(normalized).parts:
                issues.append(
                    ValidationIssue(
                        "unsafe-path",
                        f"Ссылка выходит за пределы каталога пособия: {target}",
                        _line_number(text, match.start()),
                    )
                )
    if "\\begin{document}" not in text or "\\end{document}" not in text:
        issues.append(ValidationIssue("document-boundary", "Не найдены обе границы document"))
    return issues
