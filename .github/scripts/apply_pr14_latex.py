from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one occurrence, found {count}")
    return text.replace(old, new, 1)


def replace_regex(text: str, pattern: str, replacement: str, *, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    return updated


# ---------------------------------------------------------------------------
# Configuration and domain reservation state
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/config.py"
text = read(path)
text = replace_once(
    text,
    "    poll_seconds: int = 60\n",
    "    poll_seconds: int = 60\n"
    "    reservation_timeout_minutes: int = Field(default=30, ge=5, le=1440)\n",
    label="latex reservation timeout",
)
write(path, text)

path = "src/tutor_assistant/domain.py"
text = read(path)
text = replace_once(
    text,
    "    tex_blob_sha: str | None = None\n",
    "    tex_blob_sha: str | None = None\n"
    "    active_operation_id: str | None = None\n"
    "    active_tex_blob_sha: str | None = None\n"
    "    active_source_commit: str | None = None\n"
    "    active_branch: str | None = None\n"
    "    active_started_at: datetime | None = None\n",
    label="latex reservation fields",
)
write(path, text)


# ---------------------------------------------------------------------------
# Remote LaTeX service: probe, exact commit compilation, non-force push
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/latex/remote.py"
text = read(path)
text = replace_once(
    text,
    "@dataclass(frozen=True)\nclass RemoteTexInfo:\n    path: str\n    blob_sha: str\n\n\n",
    "@dataclass(frozen=True)\n"
    "class RemoteTexInfo:\n"
    "    path: str\n"
    "    blob_sha: str\n\n\n"
    "@dataclass(frozen=True)\n"
    "class RemoteTexProbe:\n"
    "    branch: str\n"
    "    remote_head: str\n"
    "    path: str\n"
    "    blob_sha: str\n\n\n"
    "@dataclass(frozen=True)\n"
    "class LatexCompilationReservation:\n"
    "    operation_id: str\n"
    "    lesson: Lesson\n"
    "    row_version: int\n"
    "    probe: RemoteTexProbe\n\n\n",
    label="remote reservation models",
)
new_methods = '''    def is_candidate(self, lesson: Lesson, *, force: bool = False) -> bool:\n        if not self.latex.enabled or not lesson.pipeline.compile_pdf:\n            return False\n        if lesson.status not in LATEX_MONITOR_STATUSES:\n            return False\n        if not lesson.publication:\n            return False\n        if not force and lesson.latex.attempt >= self.latex.max_attempts:\n            return False\n        return True\n\n    def probe_lesson(self, lesson: Lesson) -> RemoteTexProbe | None:\n        if not lesson.publication:\n            return None\n        branch = lesson.publication.branch\n        remote_ref = f"{self.repository.remote}/{branch}"\n        try:\n            run_git(self.repo, "fetch", self.repository.remote, branch)\n        except GitError as exc:\n            if _is_missing_remote_ref(exc):\n                return None\n            raise\n        remote_head = run_git(self.repo, "rev-parse", remote_ref)\n        handbook = f"{lesson.publication.repository_path}/handbook"\n        names = run_git(\n            self.repo,\n            "ls-tree",\n            "-r",\n            "--name-only",\n            remote_head,\n            handbook,\n        ).splitlines()\n        candidates = sorted(name for name in names if name.lower().endswith(".tex"))\n        if not candidates:\n            return None\n        path = candidates[-1]\n        blob_sha = run_git(self.repo, "rev-parse", f"{remote_head}:{path}")\n        return RemoteTexProbe(\n            branch=branch,\n            remote_head=remote_head,\n            path=path,\n            blob_sha=blob_sha,\n        )\n\n    def find_tex(self, lesson: Lesson) -> RemoteTexInfo | None:\n        probe = self.probe_lesson(lesson)\n        if probe is None:\n            return None\n        return RemoteTexInfo(probe.path, probe.blob_sha)\n\n    def is_ready(self, lesson: Lesson) -> bool:\n        if not self.is_candidate(lesson):\n            return False\n        probe = self.probe_lesson(lesson)\n        if probe is None:\n            return False\n        if probe.blob_sha == lesson.latex.tex_blob_sha and lesson.status in {\n            JobStatus.COMPILE_FAILED,\n            JobStatus.PDF_REVIEW_REQUIRED,\n        }:\n            return False\n        return True\n\n    def compile_lesson(\n        self,\n        lesson: Lesson,\n        *,\n        force: bool = False,\n        cache_dir: Path | None = None,\n    ) -> RemoteCompilationResult:\n        if not self.is_candidate(lesson, force=force):\n            raise RuntimeError("Занятие не готово к LaTeX-компиляции")\n        probe = self.probe_lesson(lesson)\n        if probe is None:\n            raise FileNotFoundError("В ветке занятия отсутствует handbook/*.tex")\n        if not force and probe.blob_sha == lesson.latex.tex_blob_sha:\n            raise RuntimeError("Эта версия LaTeX уже компилировалась")\n        candidate = lesson.model_copy(deep=True)\n        if candidate.status in {JobStatus.PUBLISHED, JobStatus.COMPILE_FAILED}:\n            candidate.transition(JobStatus.GENERATED_TEX)\n        elif candidate.status not in {JobStatus.GENERATED_TEX, JobStatus.COMPILING_PDF}:\n            candidate.transition(JobStatus.GENERATED_TEX, force=True)\n        if candidate.status != JobStatus.COMPILING_PDF:\n            candidate.transition(JobStatus.COMPILING_PDF)\n        candidate.latex.attempt += 1\n        return self._compile_with_probe(candidate, probe, cache_dir=cache_dir)\n\n    def compile_reserved(\n        self,\n        reservation: LatexCompilationReservation,\n        *,\n        cache_dir: Path | None = None,\n    ) -> RemoteCompilationResult:\n        if reservation.lesson.status != JobStatus.COMPILING_PDF:\n            raise RuntimeError("LaTeX reservation не находится в состоянии compiling_pdf")\n        return self._compile_with_probe(\n            reservation.lesson,\n            reservation.probe,\n            cache_dir=cache_dir,\n        )\n\n    def _compile_with_probe(\n        self,\n        lesson: Lesson,\n        probe: RemoteTexProbe,\n        *,\n        cache_dir: Path | None,\n    ) -> RemoteCompilationResult:\n        if not lesson.publication:\n            raise RuntimeError("В lesson.json отсутствуют сведения о Git-публикации")\n        root = self.repo.parent / ".tutor-assistant-worktrees"\n        root.mkdir(parents=True, exist_ok=True)\n        worktree = Path(tempfile.mkdtemp(prefix="latex-", dir=root))\n        worktree.rmdir()\n        try:\n            run_git(self.repo, "worktree", "add", "--detach", str(worktree), probe.remote_head)\n            tex_file = worktree / probe.path\n            lesson_root = worktree / lesson.publication.repository_path\n            report_dir = lesson_root / "reports" / "latex"\n            preview_dir = lesson_root / "preview" / "pdf"\n            candidate = lesson.model_copy(deep=True)\n            candidate.latex.tex_path = probe.path\n            compilation = LatexCompiler(self.latex).compile(\n                tex_file,\n                attempt=candidate.latex.attempt,\n                report_dir=report_dir,\n                preview_dir=preview_dir,\n            )\n            candidate.latex.report_path = str(\n                compilation.report_file.relative_to(worktree).as_posix()\n            )\n            candidate.latex.preview_paths = [\n                str(path.relative_to(worktree).as_posix()) for path in compilation.preview_files\n            ]\n            if compilation.success and compilation.pdf_file:\n                candidate.latex.pdf_path = str(\n                    compilation.pdf_file.relative_to(worktree).as_posix()\n                )\n                candidate.transition(JobStatus.PDF_REVIEW_REQUIRED, force=True)\n            else:\n                candidate.transition(JobStatus.COMPILE_FAILED, force=True)\n\n            self._rewrite_report_paths(compilation.report_file, worktree)\n            published_candidate = candidate.model_copy(deep=True)\n            published_candidate.latex.active_operation_id = None\n            published_candidate.latex.active_tex_blob_sha = None\n            published_candidate.latex.active_source_commit = None\n            published_candidate.latex.active_branch = None\n            published_candidate.latex.active_started_at = None\n            published_candidate.write_json(lesson_root / "lesson.json")\n            self._write_job_status(lesson_root, published_candidate, compilation)\n            run_git(worktree, "add", str(lesson_root.relative_to(worktree)))\n            status = "success" if compilation.success else "failed"\n            run_git(\n                worktree,\n                "commit",\n                "-m",\n                f"Compile lesson PDF ({status}, attempt {candidate.latex.attempt})",\n            )\n            commit = run_git(worktree, "rev-parse", "HEAD")\n            # No force: if the remote branch advanced after the probe, Git rejects the push.\n            run_git(\n                worktree,\n                "push",\n                self.repository.remote,\n                f"HEAD:refs/heads/{probe.branch}",\n            )\n            if cache_dir:\n                try:\n                    self._cache_result(compilation, cache_dir)\n                except OSError as exc:\n                    compilation.warnings.append(\n                        f"Не удалось создать локальный кэш предпросмотра: {exc}"\n                    )\n            return RemoteCompilationResult(candidate, compilation, probe.branch, commit)\n        finally:\n            if worktree.exists():\n                try:\n                    run_git(self.repo, "worktree", "remove", "--force", str(worktree))\n                finally:\n                    if worktree.exists():\n                        shutil.rmtree(worktree, ignore_errors=True)\n\n'''
text = replace_regex(
    text,
    r"    def find_tex\(self, lesson: Lesson\) -> RemoteTexInfo \| None:.*?(?=    @staticmethod\n    def _cache_result)",
    new_methods,
    label="remote latex workflow",
)
write(path, text)

path = "src/tutor_assistant/latex/__init__.py"
text = read(path)
text = replace_once(
    text,
    "from .remote import RemoteLatexService\n",
    "from .remote import (\n"
    "    LatexCompilationReservation,\n"
    "    RemoteCompilationResult,\n"
    "    RemoteLatexService,\n"
    "    RemoteTexProbe,\n"
    ")\n",
    label="latex exports import",
)
text = replace_once(
    text,
    '    "LatexCompiler",\n    "RemoteLatexService",\n',
    '    "LatexCompilationReservation",\n'
    '    "LatexCompiler",\n'
    '    "RemoteCompilationResult",\n'
    '    "RemoteLatexService",\n'
    '    "RemoteTexProbe",\n',
    label="latex exports list",
)
write(path, text)


# ---------------------------------------------------------------------------
# Pipeline owns short reservation/finalization leases
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/pipeline.py"
text = read(path)
text = replace_once(
    text,
    "import logging\nfrom copy import deepcopy\nfrom pathlib import Path\n",
    "import logging\n"
    "from copy import deepcopy\n"
    "from datetime import UTC, datetime, timedelta\n"
    "from pathlib import Path\n"
    "from uuid import uuid4\n",
    label="pipeline imports",
)
text = replace_once(
    text,
    "from .publisher import LessonPublisher, PublicationResult\n",
    "from .latex.remote import (\n"
    "    LatexCompilationReservation,\n"
    "    RemoteCompilationResult,\n"
    "    RemoteLatexService,\n"
    "    RemoteTexProbe,\n"
    ")\n"
    "from .publisher import LessonPublisher, PublicationResult\n",
    label="pipeline latex imports",
)
methods = '''    @staticmethod\n    def _clear_latex_reservation(lesson: Lesson) -> None:\n        lesson.latex.active_operation_id = None\n        lesson.latex.active_tex_blob_sha = None\n        lesson.latex.active_source_commit = None\n        lesson.latex.active_branch = None\n        lesson.latex.active_started_at = None\n\n    def reserve_remote_latex(\n        self,\n        lesson: Lesson,\n        probe: RemoteTexProbe,\n        *,\n        force: bool = False,\n    ) -> LatexCompilationReservation | None:\n        with self.content_service.activity(\n            "latex-reservation",\n            lesson_id=lesson.lesson_id,\n            exclusive=True,\n        ):\n            content = self.content_service.get_lesson(lesson.lesson_id)\n            current = content.lesson\n            now = datetime.now(UTC)\n            if current.latex.active_operation_id:\n                started = current.latex.active_started_at\n                timeout = timedelta(minutes=self.config.latex.reservation_timeout_minutes)\n                if started is None or now - started < timeout:\n                    return None\n                self._clear_latex_reservation(current)\n                if current.status == JobStatus.COMPILING_PDF:\n                    current.transition(\n                        JobStatus.COMPILE_FAILED,\n                        "Предыдущая компиляция была прервана до сохранения результата",\n                        force=True,\n                    )\n            if not force and probe.blob_sha == current.latex.tex_blob_sha and current.status in {\n                JobStatus.COMPILE_FAILED,\n                JobStatus.PDF_REVIEW_REQUIRED,\n            }:\n                return None\n            if not force and current.latex.attempt >= self.config.latex.max_attempts:\n                return None\n            if current.status in {JobStatus.PUBLISHED, JobStatus.COMPILE_FAILED}:\n                current.transition(JobStatus.GENERATED_TEX)\n            elif current.status not in {JobStatus.GENERATED_TEX, JobStatus.COMPILING_PDF}:\n                current.transition(JobStatus.GENERATED_TEX, force=True)\n            if current.status != JobStatus.COMPILING_PDF:\n                current.transition(JobStatus.COMPILING_PDF)\n            operation_id = uuid4().hex\n            current.latex.attempt += 1\n            current.latex.tex_path = probe.path\n            current.latex.active_operation_id = operation_id\n            current.latex.active_tex_blob_sha = probe.blob_sha\n            current.latex.active_source_commit = probe.remote_head\n            current.latex.active_branch = probe.branch\n            current.latex.active_started_at = now\n            stored = self.save_state(\n                current,\n                "latex",\n                "status",\n                "error",\n                force_status=True,\n                expected_row_version=content.row_version,\n            )\n            row_version = self.content_service.repository.lesson_row_version(stored.lesson_id)\n            logging.info(\n                "LaTeX reserved: lesson=%s operation=%s blob=%s",\n                stored.lesson_id,\n                operation_id,\n                probe.blob_sha,\n            )\n            return LatexCompilationReservation(\n                operation_id=operation_id,\n                lesson=stored.model_copy(deep=True),\n                row_version=row_version,\n                probe=probe,\n            )\n\n    def finalize_remote_latex(\n        self,\n        reservation: LatexCompilationReservation,\n        *,\n        result: RemoteCompilationResult | None = None,\n        error: str | None = None,\n    ) -> RemoteCompilationResult | None:\n        with self.content_service.activity(\n            "latex-finalize",\n            lesson_id=reservation.lesson.lesson_id,\n            exclusive=True,\n        ):\n            content = self.content_service.get_lesson(reservation.lesson.lesson_id)\n            current = content.lesson\n            if current.latex.active_operation_id != reservation.operation_id:\n                logging.warning(\n                    "Stale LaTeX finalize ignored: lesson=%s operation=%s active=%s",\n                    current.lesson_id,\n                    reservation.operation_id,\n                    current.latex.active_operation_id,\n                )\n                return None\n            if result is not None:\n                compiled = result.lesson\n                current.latex.pdf_path = compiled.latex.pdf_path\n                current.latex.report_path = compiled.latex.report_path\n                current.latex.preview_paths = list(compiled.latex.preview_paths)\n                current.latex.tex_path = reservation.probe.path\n                current.latex.tex_blob_sha = reservation.probe.blob_sha\n                current.transition(compiled.status, compiled.error, force=True)\n            else:\n                current.latex.tex_path = reservation.probe.path\n                current.latex.tex_blob_sha = reservation.probe.blob_sha\n                current.transition(\n                    JobStatus.COMPILE_FAILED,\n                    error or "LaTeX-компиляция завершилась с ошибкой",\n                    force=True,\n                )\n            self._clear_latex_reservation(current)\n            stored = self.save_state(\n                current,\n                "latex",\n                "status",\n                "error",\n                force_status=True,\n                expected_row_version=content.row_version,\n            )\n            logging.info(\n                "LaTeX finalized: lesson=%s operation=%s status=%s",\n                stored.lesson_id,\n                reservation.operation_id,\n                stored.status.value,\n            )\n            if result is None:\n                return None\n            result.lesson = stored\n            return result\n\n    def compile_remote_latex(\n        self,\n        lesson: Lesson,\n        *,\n        force: bool = False,\n        cache_dir: Path | None = None,\n    ) -> RemoteCompilationResult:\n        service = RemoteLatexService(self.config.repository, self.config.latex)\n        probe = service.probe_lesson(lesson)\n        if probe is None:\n            raise FileNotFoundError("В ветке занятия отсутствует handbook/*.tex")\n        reservation = self.reserve_remote_latex(lesson, probe, force=force)\n        if reservation is None:\n            raise RuntimeError("LaTeX-компиляция уже выполняется или версия уже обработана")\n        try:\n            result = service.compile_reserved(\n                reservation,\n                cache_dir=cache_dir or self.lesson_dir(reservation.lesson) / "latex-cache",\n            )\n        except Exception as exc:\n            self.finalize_remote_latex(reservation, error=str(exc))\n            raise\n        finalized = self.finalize_remote_latex(reservation, result=result)\n        if finalized is None:\n            raise RuntimeError("Результат LaTeX устарел до финализации")\n        self._replace_lesson(lesson, finalized.lesson)\n        return finalized\n\n    def scan_remote_latex(self) -> RemoteCompilationResult | None:\n        service = RemoteLatexService(self.config.repository, self.config.latex)\n        for lesson in self.store.list():\n            if not service.is_candidate(lesson):\n                continue\n            probe = service.probe_lesson(lesson)\n            if probe is None:\n                continue\n            reservation = self.reserve_remote_latex(lesson, probe)\n            if reservation is None:\n                continue\n            try:\n                result = service.compile_reserved(\n                    reservation,\n                    cache_dir=self.lesson_dir(reservation.lesson) / "latex-cache",\n                )\n            except Exception as exc:\n                self.finalize_remote_latex(reservation, error=str(exc))\n                raise\n            finalized = self.finalize_remote_latex(reservation, result=result)\n            if finalized is not None:\n                return finalized\n        return None\n\n'''
text = replace_once(
    text,
    "    def transcribe(self, lesson: Lesson, audio: Path) -> Lesson:\n",
    methods + "    def transcribe(self, lesson: Lesson, audio: Path) -> Lesson:\n",
    label="pipeline reservation methods",
)
write(path, text)


# ---------------------------------------------------------------------------
# Production GUI and CLI use reservation workflow without coarse lease
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/ui/concurrent_app.py"
text = read(path)
text = replace_once(
    text,
    "                operation=lambda: scan_remote_latex(\n"
    "                    self.config.repository,\n"
    "                    self.config.latex,\n"
    "                    self.pipeline.store.list(),\n"
    "                    lambda lesson: self.pipeline.lesson_dir(lesson) / \"latex-cache\",\n"
    "                ),\n"
    "                activity=\"latex-monitor\",\n",
    "                operation=self.pipeline.scan_remote_latex,\n",
    label="gui reservation scan",
)
write(path, text)

path = "src/tutor_assistant/cli.py"
text = read(path)
old = '''        with pipeline.content_service.activity("latex-compilation", lesson_id=lesson.lesson_id):\n            result = RemoteLatexService(config.repository, config.latex).compile_lesson(\n                lesson,\n                force=args.force,\n                cache_dir=pipeline.lesson_dir(lesson) / "latex-cache",\n            )\n        pipeline.save_state(\n            result.lesson,\n            "latex",\n            "status",\n            "error",\n            force_status=True,\n        )\n'''
new = '''        result = pipeline.compile_remote_latex(\n            lesson,\n            force=args.force,\n            cache_dir=pipeline.lesson_dir(lesson) / "latex-cache",\n        )\n'''
text = replace_once(text, old, new, label="cli reservation compile")
write(path, text)


# ---------------------------------------------------------------------------
# GUI diagnostics show scan metrics
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/ui/content_health.py"
text = read(path)
text = replace_once(
    text,
    "            f\"({report.fts_documents} документов)\"\n        )\n",
    "            f\"({report.fts_documents} документов) · \"\n"
    "            f\"режим: {report.scan.mode.value} · \"\n"
    "            f\"проверено занятий: {report.scan.lessons_examined} · \"\n"
    "            f\"SHA-256: {report.scan.assets_hashed} · \"\n"
    "            f\"cache hits: {report.scan.asset_cache_hits} · \"\n"
    "            f\"{report.scan.duration_ms} мс\"\n"
    "        )\n",
    label="health scan metrics",
)
write(path, text)


# ---------------------------------------------------------------------------
# Reservation tests
# ---------------------------------------------------------------------------
write(
    "tests/test_latex_reservation.py",
    '''from __future__ import annotations\n\nfrom datetime import UTC, date, datetime, timedelta\nfrom pathlib import Path\n\nimport pytest\n\nfrom tutor_assistant.config import AppConfig\nfrom tutor_assistant.domain import JobStatus, Lesson, PublicationInfo, Student\nfrom tutor_assistant.latex.models import CompilationResult\nfrom tutor_assistant.latex.remote import (\n    LatexCompilationReservation,\n    RemoteCompilationResult,\n    RemoteLatexService,\n    RemoteTexProbe,\n)\nfrom tutor_assistant.pipeline import LessonPipeline\n\n\ndef create_pipeline(tmp_path: Path, lesson_id: str = "latex-reservation") -> tuple[LessonPipeline, Lesson]:\n    config = AppConfig(workspace=tmp_path / "data")\n    config.repository.students_repo = tmp_path / "students"\n    config.repository.students_repo.mkdir()\n    pipeline = LessonPipeline(config)\n    lesson = Lesson(\n        lesson_id=lesson_id,\n        student=Student(id="student", full_name="Ученик"),\n        subject="mathematics",\n        lesson_date=date(2026, 7, 21),\n        topic="Reservation",\n    )\n    lesson.transition(JobStatus.PUBLISHED, force=True)\n    lesson.publication = PublicationInfo(\n        branch="lesson-branch",\n        repository_path=f"students/student/{lesson_id}",\n        commit="base",\n    )\n    pipeline.create(lesson)\n    return pipeline, lesson\n\n\ndef make_probe(blob: str = "blob-1") -> RemoteTexProbe:\n    return RemoteTexProbe(\n        branch="lesson-branch",\n        remote_head="remote-head",\n        path="students/student/lesson/handbook/lesson.tex",\n        blob_sha=blob,\n    )\n\n\ndef make_result(tmp_path: Path, reservation: LatexCompilationReservation) -> RemoteCompilationResult:\n    tex = tmp_path / "lesson.tex"\n    pdf = tmp_path / "lesson.pdf"\n    log = tmp_path / "lesson.log"\n    report = tmp_path / "lesson.json"\n    for path in (tex, pdf, log, report):\n        path.write_text("payload", encoding="utf-8")\n    lesson = reservation.lesson.model_copy(deep=True)\n    lesson.latex.pdf_path = "lesson.pdf"\n    lesson.latex.report_path = "reports/latex/compilation.json"\n    lesson.transition(JobStatus.PDF_REVIEW_REQUIRED, force=True)\n    compilation = CompilationResult(\n        success=True,\n        tex_file=tex,\n        pdf_file=pdf,\n        log_file=log,\n        report_file=report,\n        duration_seconds=1.0,\n    )\n    return RemoteCompilationResult(lesson, compilation, reservation.probe.branch, "commit")\n\n\ndef test_reservation_is_atomic_and_duplicate_is_rejected(tmp_path: Path) -> None:\n    pipeline, lesson = create_pipeline(tmp_path)\n    probe = make_probe()\n\n    first = pipeline.reserve_remote_latex(lesson, probe)\n    second = pipeline.reserve_remote_latex(lesson, probe)\n\n    assert first is not None\n    assert second is None\n    current = pipeline.content_service.get_lesson(lesson.lesson_id).lesson\n    assert current.status == JobStatus.COMPILING_PDF\n    assert current.latex.active_operation_id == first.operation_id\n    assert current.latex.active_tex_blob_sha == probe.blob_sha\n\n\ndef test_finalize_applies_only_matching_operation(tmp_path: Path) -> None:\n    pipeline, lesson = create_pipeline(tmp_path)\n    reservation = pipeline.reserve_remote_latex(lesson, make_probe())\n    assert reservation is not None\n    result = make_result(tmp_path, reservation)\n    stale = LatexCompilationReservation(\n        operation_id="other",\n        lesson=reservation.lesson,\n        row_version=reservation.row_version,\n        probe=reservation.probe,\n    )\n\n    assert pipeline.finalize_remote_latex(stale, result=result) is None\n    finalized = pipeline.finalize_remote_latex(reservation, result=result)\n\n    assert finalized is not None\n    current = pipeline.content_service.get_lesson(lesson.lesson_id).lesson\n    assert current.status == JobStatus.PDF_REVIEW_REQUIRED\n    assert current.latex.tex_blob_sha == reservation.probe.blob_sha\n    assert current.latex.active_operation_id is None\n\n\ndef test_stale_reservation_is_recovered(tmp_path: Path) -> None:\n    pipeline, lesson = create_pipeline(tmp_path)\n    first = pipeline.reserve_remote_latex(lesson, make_probe("blob-old"))\n    assert first is not None\n    content = pipeline.content_service.get_lesson(lesson.lesson_id)\n    current = content.lesson\n    current.latex.active_started_at = datetime.now(UTC) - timedelta(hours=2)\n    pipeline.save_state(\n        current,\n        "latex",\n        "status",\n        "error",\n        force_status=True,\n        expected_row_version=content.row_version,\n    )\n\n    replacement = pipeline.reserve_remote_latex(lesson, make_probe("blob-new"), force=True)\n\n    assert replacement is not None\n    assert replacement.operation_id != first.operation_id\n    assert replacement.lesson.latex.attempt == 2\n\n\ndef test_remote_compile_runs_without_content_lease(tmp_path: Path, monkeypatch) -> None:\n    pipeline, lesson = create_pipeline(tmp_path)\n    probe = make_probe()\n    observed: list[list[str]] = []\n\n    monkeypatch.setattr(RemoteLatexService, "probe_lesson", lambda self, item: probe)\n\n    def compile_without_lease(self, reservation, *, cache_dir=None):\n        observed.append([item.activity for item in pipeline.content_service.active_activities()])\n        return make_result(tmp_path, reservation)\n\n    monkeypatch.setattr(RemoteLatexService, "compile_reserved", compile_without_lease)\n    result = pipeline.scan_remote_latex()\n\n    assert result is not None\n    assert observed == [[]]\n\n\ndef test_probe_uses_exact_remote_head(tmp_path: Path, monkeypatch) -> None:\n    pipeline, lesson = create_pipeline(tmp_path)\n    calls: list[tuple[str, ...]] = []\n\n    def fake_git(_repo: Path, *args: str, **_kwargs) -> str:\n        calls.append(args)\n        if args[:2] == ("fetch", "origin"):\n            return ""\n        if args == ("rev-parse", "origin/lesson-branch"):\n            return "abc123"\n        if args[:4] == ("ls-tree", "-r", "--name-only", "abc123"):\n            return "students/student/latex-reservation/handbook/lesson.tex\\n"\n        if args == (\n            "rev-parse",\n            "abc123:students/student/latex-reservation/handbook/lesson.tex",\n        ):\n            return "blob123"\n        raise AssertionError(args)\n\n    monkeypatch.setattr("tutor_assistant.latex.remote.run_git", fake_git)\n    probe = RemoteLatexService(\n        pipeline.config.repository,\n        pipeline.config.latex,\n    ).probe_lesson(lesson)\n\n    assert probe is not None\n    assert probe.remote_head == "abc123"\n    assert probe.blob_sha == "blob123"\n''',
)

print("PR 14 LaTeX patch applied")
