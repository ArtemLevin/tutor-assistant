from pathlib import Path

from tutor_assistant.config import LatexConfig
from tutor_assistant.latex.compiler import LatexCompiler
from tutor_assistant.latex.validator import validate_tex


SAFE_DOCUMENT = r"""
\documentclass{article}
\begin{document}
Безопасный текст.
\end{document}
"""


def test_safe_document_passes_validation(tmp_path: Path) -> None:
    tex = tmp_path / "safe.tex"
    tex.write_text(SAFE_DOCUMENT, encoding="utf-8")
    assert validate_tex(tex) == []


def test_shell_escape_command_is_rejected(tmp_path: Path) -> None:
    tex = tmp_path / "unsafe.tex"
    tex.write_text(SAFE_DOCUMENT.replace("Безопасный текст.", r"\immediate\write18{calc.exe}"), encoding="utf-8")
    issues = validate_tex(tex)
    assert any(issue.code == "shell-command" for issue in issues)


def test_parent_path_is_rejected(tmp_path: Path) -> None:
    tex = tmp_path / "unsafe-path.tex"
    tex.write_text(SAFE_DOCUMENT.replace("Безопасный текст.", r"\input{../secret}"), encoding="utf-8")
    assert any(issue.code == "unsafe-path" for issue in validate_tex(tex))


def test_security_failure_creates_report_without_tex_installation(tmp_path: Path) -> None:
    tex = tmp_path / "unsafe.tex"
    tex.write_text(SAFE_DOCUMENT.replace("Безопасный текст.", r"\openin1=secret.txt"), encoding="utf-8")
    result = LatexCompiler(LatexConfig()).compile(tex)
    assert not result.success
    assert result.report_file.exists()
    assert result.fix_request_file.exists()
