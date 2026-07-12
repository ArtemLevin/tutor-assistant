from pathlib import Path

from tutor_assistant.config import LatexConfig
from tutor_assistant.latex import compiler as compiler_module
from tutor_assistant.latex.compiler import LatexCompiler
from tutor_assistant.latex.models import EnvironmentReport


DOCUMENT = r"\documentclass{article}\begin{document}OK\end{document}"


def test_successful_compilation_pipeline_without_real_tex(monkeypatch, tmp_path: Path) -> None:
    tex = tmp_path / "lesson.tex"
    tex.write_text(DOCUMENT, encoding="utf-8")

    monkeypatch.setattr(
        compiler_module,
        "inspect_latex_environment",
        lambda config: EnvironmentReport(True, "latexmk", "pdflatex", None),
    )

    def fake_run(command, cwd, timeout, environment=None):
        output = cwd / ".tutor-build" / "lesson.pdf"
        output.write_bytes(b"%PDF-1.4 test")
        return 0, "Latexmk: All targets are up-to-date", False

    monkeypatch.setattr(compiler_module, "_run_with_timeout", fake_run)
    monkeypatch.setattr(compiler_module, "_inspect_pdf", lambda pdf: (2, pdf.stat().st_size, []))
    result = LatexCompiler(LatexConfig(render_preview=False)).compile(tex)
    assert result.success
    assert result.pdf_file == tex.with_suffix(".pdf")
    assert result.pages == 2
    assert result.report_file.exists()


def test_timeout_becomes_clear_error(monkeypatch, tmp_path: Path) -> None:
    tex = tmp_path / "lesson.tex"
    tex.write_text(DOCUMENT, encoding="utf-8")
    monkeypatch.setattr(
        compiler_module,
        "inspect_latex_environment",
        lambda config: EnvironmentReport(True, "latexmk", "pdflatex", None),
    )
    monkeypatch.setattr(
        compiler_module, "_run_with_timeout", lambda *args, **kwargs: (-1, "partial log", True)
    )
    result = LatexCompiler(LatexConfig(render_preview=False, timeout_seconds=5)).compile(tex)
    assert not result.success
    assert result.timed_out
    assert "5 секунд" in result.errors[0]
