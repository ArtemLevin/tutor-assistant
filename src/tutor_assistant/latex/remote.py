from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import LatexConfig, RepositoryConfig
from ..domain import JobStatus, Lesson
from ..publisher import run_git
from .compiler import LatexCompiler
from .models import CompilationResult


@dataclass(frozen=True)
class RemoteTexInfo:
    path: str
    blob_sha: str


@dataclass
class RemoteCompilationResult:
    lesson: Lesson
    compilation: CompilationResult
    branch: str
    commit: str


class RemoteLatexService:
    def __init__(self, repository: RepositoryConfig, latex: LatexConfig) -> None:
        self.repository = repository
        self.latex = latex
        self.repo = repository.students_repo.resolve()

    def find_tex(self, lesson: Lesson) -> RemoteTexInfo | None:
        if not lesson.publication:
            return None
        branch = lesson.publication.branch
        remote_ref = f"{self.repository.remote}/{branch}"
        run_git(self.repo, "fetch", self.repository.remote, branch)
        handbook = f"{lesson.publication.repository_path}/handbook"
        names = run_git(self.repo, "ls-tree", "-r", "--name-only", remote_ref, handbook).splitlines()
        candidates = sorted(name for name in names if name.lower().endswith(".tex"))
        if not candidates:
            return None
        path = candidates[-1]
        blob_sha = run_git(self.repo, "rev-parse", f"{remote_ref}:{path}")
        return RemoteTexInfo(path, blob_sha)

    def is_ready(self, lesson: Lesson) -> bool:
        if not self.latex.enabled or not lesson.pipeline.compile_pdf:
            return False
        if not lesson.publication or lesson.latex.attempt >= self.latex.max_attempts:
            return False
        info = self.find_tex(lesson)
        if not info:
            return False
        if info.blob_sha == lesson.latex.tex_blob_sha and lesson.status in {
            JobStatus.COMPILE_FAILED,
            JobStatus.PDF_REVIEW_REQUIRED,
        }:
            return False
        return True

    def compile_lesson(
        self, lesson: Lesson, *, force: bool = False, cache_dir: Path | None = None
    ) -> RemoteCompilationResult:
        if not lesson.publication:
            raise RuntimeError("В lesson.json отсутствуют сведения о Git-публикации")
        info = self.find_tex(lesson)
        if not info:
            raise FileNotFoundError("В ветке занятия отсутствует handbook/*.tex")
        if not force and info.blob_sha == lesson.latex.tex_blob_sha:
            raise RuntimeError("Эта версия LaTeX уже компилировалась")
        if not force and lesson.latex.attempt >= self.latex.max_attempts:
            raise RuntimeError("Исчерпано максимальное количество попыток компиляции")

        branch = lesson.publication.branch
        remote_ref = f"{self.repository.remote}/{branch}"
        root = self.repo.parent / ".tutor-assistant-worktrees"
        root.mkdir(parents=True, exist_ok=True)
        worktree = Path(tempfile.mkdtemp(prefix="latex-", dir=root))
        worktree.rmdir()
        try:
            run_git(self.repo, "worktree", "add", "--detach", str(worktree), remote_ref)
            tex_file = worktree / info.path
            lesson_root = worktree / lesson.publication.repository_path
            report_dir = lesson_root / "reports" / "latex"
            preview_dir = lesson_root / "preview" / "pdf"
            candidate = lesson.model_copy(deep=True)
            if candidate.status in {JobStatus.PUBLISHED, JobStatus.COMPILE_FAILED}:
                candidate.transition(JobStatus.GENERATED_TEX)
            elif candidate.status not in {JobStatus.GENERATED_TEX, JobStatus.COMPILING_PDF}:
                candidate.transition(JobStatus.GENERATED_TEX, force=True)
            if candidate.status != JobStatus.COMPILING_PDF:
                candidate.transition(JobStatus.COMPILING_PDF)
            candidate.latex.attempt += 1
            candidate.latex.tex_path = info.path
            candidate.latex.tex_blob_sha = info.blob_sha
            compilation = LatexCompiler(self.latex).compile(
                tex_file,
                attempt=candidate.latex.attempt,
                report_dir=report_dir,
                preview_dir=preview_dir,
            )
            candidate.latex.report_path = str(compilation.report_file.relative_to(worktree).as_posix())
            candidate.latex.preview_paths = [
                str(path.relative_to(worktree).as_posix()) for path in compilation.preview_files
            ]
            if compilation.success and compilation.pdf_file:
                candidate.latex.pdf_path = str(compilation.pdf_file.relative_to(worktree).as_posix())
                candidate.transition(JobStatus.PDF_REVIEW_REQUIRED)
            else:
                candidate.transition(JobStatus.COMPILE_FAILED)

            self._rewrite_report_paths(compilation.report_file, worktree)
            candidate.write_json(lesson_root / "lesson.json")
            self._write_job_status(lesson_root, candidate, compilation)
            run_git(worktree, "add", str(lesson_root.relative_to(worktree)))
            status = "success" if compilation.success else "failed"
            run_git(
                worktree,
                "commit",
                "-m",
                f"Compile lesson PDF ({status}, attempt {candidate.latex.attempt})",
            )
            commit = run_git(worktree, "rev-parse", "HEAD")
            run_git(worktree, "push", self.repository.remote, f"HEAD:refs/heads/{branch}")
            if cache_dir:
                try:
                    self._cache_result(compilation, cache_dir)
                except OSError as exc:
                    compilation.warnings.append(f"Не удалось создать локальный кэш предпросмотра: {exc}")
            return RemoteCompilationResult(candidate, compilation, branch, commit)
        finally:
            if worktree.exists():
                try:
                    run_git(self.repo, "worktree", "remove", "--force", str(worktree))
                finally:
                    if worktree.exists():
                        shutil.rmtree(worktree, ignore_errors=True)

    @staticmethod
    def _cache_result(result: CompilationResult, destination: Path) -> None:
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        tex = destination / result.tex_file.name
        shutil.copy2(result.tex_file, tex)
        result.tex_file = tex
        if result.pdf_file:
            pdf = destination / result.pdf_file.name
            shutil.copy2(result.pdf_file, pdf)
            result.pdf_file = pdf
        log = destination / "compilation.log"
        report = destination / "compilation.json"
        shutil.copy2(result.log_file, log)
        shutil.copy2(result.report_file, report)
        result.log_file = log
        result.report_file = report
        if result.fix_request_file:
            fix = destination / "latex_fix_request.md"
            shutil.copy2(result.fix_request_file, fix)
            result.fix_request_file = fix
        previews = destination / "preview"
        previews.mkdir()
        cached_previews: list[Path] = []
        for source in result.preview_files:
            target = previews / source.name
            shutil.copy2(source, target)
            cached_previews.append(target)
        result.preview_files = cached_previews

    @staticmethod
    def _rewrite_report_paths(report: Path, worktree: Path) -> None:
        payload = json.loads(report.read_text(encoding="utf-8"))
        for key in ("tex_file", "pdf_file", "log_file", "report_file", "fix_request_file"):
            value = payload.get(key)
            if value:
                path = Path(value)
                try:
                    payload[key] = path.relative_to(worktree).as_posix()
                except ValueError:
                    payload[key] = path.name
        payload["preview_files"] = [
            Path(value).relative_to(worktree).as_posix()
            if Path(value).is_relative_to(worktree)
            else Path(value).name
            for value in payload.get("preview_files", [])
        ]
        report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_job_status(self, root: Path, lesson: Lesson, result: CompilationResult) -> None:
        path = root / "job.status.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        payload.update(
            {
                "status": lesson.status.value,
                "stage": "latex",
                "artifacts": {
                    **payload.get("artifacts", {}),
                    "tex": "completed",
                    "pdf": "completed" if result.success else "failed",
                },
                "latex": {
                    "attempt": lesson.latex.attempt,
                    "max_attempts": self.latex.max_attempts,
                    "pdf": lesson.latex.pdf_path,
                    "report": lesson.latex.report_path,
                    "pages": result.pages,
                    "size_bytes": result.size_bytes,
                    "errors": result.errors[:10],
                },
            }
        )
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
