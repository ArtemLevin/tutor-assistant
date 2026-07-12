from __future__ import annotations

from tutor_assistant.diagnostics import DiagnosticCheck, DiagnosticReport, format_diagnostics


def test_report_counts_required_errors_and_warnings() -> None:
    report = DiagnosticReport(
        ready=False,
        checks=(
            DiagnosticCheck("Python", "ok", "3.13"),
            DiagnosticCheck("Git", "error", "missing"),
            DiagnosticCheck("FFmpeg", "warning", "missing", required=False),
        ),
    )

    assert report.errors == 1
    assert report.warnings == 1
    assert report.to_dict()["ready"] is False


def test_human_report_contains_summary() -> None:
    report = DiagnosticReport(
        ready=True,
        checks=(DiagnosticCheck("Python", "ok", "3.13"),),
    )

    output = format_diagnostics(report)

    assert "[OK] Python" in output
    assert "Итог: ГОТОВО" in output
