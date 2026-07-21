from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tutor_assistant.config import AppConfig
from tutor_assistant.domain import JobStatus, Lesson, PublicationInfo, Student
from tutor_assistant.latex.models import CompilationResult
from tutor_assistant.latex.remote import (
    LatexCompilationReservation,
    RemoteCompilationResult,
    RemoteLatexService,
    RemoteTexProbe,
)
from tutor_assistant.pipeline import LessonPipeline


def create_pipeline(tmp_path: Path, lesson_id: str = "latex-reservation") -> tuple[LessonPipeline, Lesson]:
    config = AppConfig(workspace=tmp_path / "data")
    config.repository.students_repo = tmp_path / "students"
    config.repository.students_repo.mkdir()
    pipeline = LessonPipeline(config)
    lesson = Lesson(
        lesson_id=lesson_id,
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 21),
        topic="Reservation",
    )
    lesson.transition(JobStatus.PUBLISHED, force=True)
    lesson.publication = PublicationInfo(
        branch="lesson-branch",
        repository_path=f"students/student/{lesson_id}",
        commit="base",
    )
    pipeline.create(lesson)
    return pipeline, lesson


def make_probe(blob: str = "blob-1") -> RemoteTexProbe:
    return RemoteTexProbe(
        branch="lesson-branch",
        remote_head="remote-head",
        path="students/student/lesson/handbook/lesson.tex",
        blob_sha=blob,
    )


def make_result(tmp_path: Path, reservation: LatexCompilationReservation) -> RemoteCompilationResult:
    tex = tmp_path / "lesson.tex"
    pdf = tmp_path / "lesson.pdf"
    log = tmp_path / "lesson.log"
    report = tmp_path / "lesson.json"
    for path in (tex, pdf, log, report):
        path.write_text("payload", encoding="utf-8")
    lesson = reservation.lesson.model_copy(deep=True)
    lesson.latex.pdf_path = "lesson.pdf"
    lesson.latex.report_path = "reports/latex/compilation.json"
    lesson.transition(JobStatus.PDF_REVIEW_REQUIRED, force=True)
    compilation = CompilationResult(
        success=True,
        tex_file=tex,
        pdf_file=pdf,
        log_file=log,
        report_file=report,
        duration_seconds=1.0,
    )
    return RemoteCompilationResult(lesson, compilation, reservation.probe.branch, "commit")


def test_reservation_is_atomic_and_duplicate_is_rejected(tmp_path: Path) -> None:
    pipeline, lesson = create_pipeline(tmp_path)
    probe = make_probe()

    first = pipeline.reserve_remote_latex(lesson, probe)
    second = pipeline.reserve_remote_latex(lesson, probe)

    assert first is not None
    assert second is None
    current = pipeline.content_service.get_lesson(lesson.lesson_id).lesson
    assert current.status == JobStatus.COMPILING_PDF
    assert current.latex.active_operation_id == first.operation_id
    assert current.latex.active_tex_blob_sha == probe.blob_sha


def test_finalize_applies_only_matching_operation(tmp_path: Path) -> None:
    pipeline, lesson = create_pipeline(tmp_path)
    reservation = pipeline.reserve_remote_latex(lesson, make_probe())
    assert reservation is not None
    result = make_result(tmp_path, reservation)
    stale = LatexCompilationReservation(
        operation_id="other",
        lesson=reservation.lesson,
        row_version=reservation.row_version,
        probe=reservation.probe,
    )

    assert pipeline.finalize_remote_latex(stale, result=result) is None
    finalized = pipeline.finalize_remote_latex(reservation, result=result)

    assert finalized is not None
    current = pipeline.content_service.get_lesson(lesson.lesson_id).lesson
    assert current.status == JobStatus.PDF_REVIEW_REQUIRED
    assert current.latex.tex_blob_sha == reservation.probe.blob_sha
    assert current.latex.active_operation_id is None


def test_stale_reservation_is_recovered(tmp_path: Path) -> None:
    pipeline, lesson = create_pipeline(tmp_path)
    first = pipeline.reserve_remote_latex(lesson, make_probe("blob-old"))
    assert first is not None
    content = pipeline.content_service.get_lesson(lesson.lesson_id)
    current = content.lesson
    current.latex.active_started_at = datetime.now(UTC) - timedelta(hours=2)
    pipeline.save_state(
        current,
        "latex",
        "status",
        "error",
        force_status=True,
        expected_row_version=content.row_version,
    )

    replacement = pipeline.reserve_remote_latex(lesson, make_probe("blob-new"), force=True)

    assert replacement is not None
    assert replacement.operation_id != first.operation_id
    assert replacement.lesson.latex.attempt == 2


def test_remote_compile_runs_without_content_lease(tmp_path: Path, monkeypatch) -> None:
    pipeline, lesson = create_pipeline(tmp_path)
    probe = make_probe()
    observed: list[list[str]] = []

    monkeypatch.setattr(RemoteLatexService, "probe_lesson", lambda self, item: probe)

    def compile_without_lease(self, reservation, *, cache_dir=None):
        observed.append([item.activity for item in pipeline.content_service.active_activities()])
        return make_result(tmp_path, reservation)

    monkeypatch.setattr(RemoteLatexService, "compile_reserved", compile_without_lease)
    result = pipeline.scan_remote_latex()

    assert result is not None
    assert observed == [[]]


def test_probe_uses_exact_remote_head(tmp_path: Path, monkeypatch) -> None:
    pipeline, lesson = create_pipeline(tmp_path)
    calls: list[tuple[str, ...]] = []

    def fake_git(_repo: Path, *args: str, **_kwargs) -> str:
        calls.append(args)
        if args[:2] == ("fetch", "origin"):
            return ""
        if args == ("rev-parse", "origin/lesson-branch"):
            return "abc123"
        if args[:4] == ("ls-tree", "-r", "--name-only", "abc123"):
            return "students/student/latex-reservation/handbook/lesson.tex\n"
        if args == (
            "rev-parse",
            "abc123:students/student/latex-reservation/handbook/lesson.tex",
        ):
            return "blob123"
        raise AssertionError(args)

    monkeypatch.setattr("tutor_assistant.latex.remote.run_git", fake_git)
    probe = RemoteLatexService(
        pipeline.config.repository,
        pipeline.config.latex,
    ).probe_lesson(lesson)

    assert probe is not None
    assert probe.remote_head == "abc123"
    assert probe.blob_sha == "blob123"
