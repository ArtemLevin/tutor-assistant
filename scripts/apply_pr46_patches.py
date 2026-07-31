from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"Patch anchor not found: {relative}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def patch_publisher() -> None:
    replace_once(
        "src/tutor_assistant/publisher.py",
        """def run_git(repo: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS) -> str:\n    result = _run_command([\"git\", *args], cwd=repo, timeout=timeout)\n    if result.returncode:\n        raise GitError(result.stderr.strip() or result.stdout.strip())\n    return result.stdout.strip()\n""",
        """def run_git(\n    repo: Path,\n    *args: str,\n    timeout: float = GIT_TIMEOUT_SECONDS,\n    allow_push: bool = False,\n) -> str:\n    if args and args[0] == \"push\" and not allow_push:\n        raise GitError(\n            \"Git push разрешён только через verified transcript publication gateway\"\n        )\n    result = _run_command([\"git\", *args], cwd=repo, timeout=timeout)\n    if result.returncode:\n        raise GitError(result.stderr.strip() or result.stdout.strip())\n    return result.stdout.strip()\n""",
    )
    replace_once(
        "src/tutor_assistant/publisher.py",
        """        if existing is not None and hashlib.sha256(existing.encode(\"utf-8\")).hexdigest() == plan.content_sha256:\n""",
        """        existing_sha256 = (\n            hashlib.sha256(existing.encode(\"utf-8\")).hexdigest()\n            if existing is not None\n            else None\n        )\n        if existing_sha256 == plan.content_sha256:\n""",
    )
    replace_once(
        "src/tutor_assistant/publisher.py",
        """            run_git(\n                worktree_path,\n                \"push\",\n                \"--porcelain\",\n                self.config.remote,\n                f\"HEAD:refs/heads/{plan.branch}\",\n            )\n""",
        """            run_git(\n                worktree_path,\n                \"push\",\n                \"--porcelain\",\n                self.config.remote,\n                f\"HEAD:refs/heads/{plan.branch}\",\n                allow_push=True,\n            )\n""",
    )


def patch_remote_latex() -> None:
    replace_once(
        "src/tutor_assistant/latex/remote.py",
        "from pathlib import Path\n",
        "from pathlib import Path, PurePosixPath\n",
    )
    replace_once(
        "src/tutor_assistant/latex/remote.py",
        """        handbook = f\"{lesson.publication.repository_path}/handbook\"\n""",
        """        publication_path = PurePosixPath(lesson.publication.repository_path)\n        lesson_repository_root = (\n            publication_path.parent\n            if publication_path.name == \"transcript.txt\"\n            else publication_path\n        )\n        handbook = f\"{lesson_repository_root.as_posix()}/handbook\"\n""",
    )
    replace_once(
        "src/tutor_assistant/latex/remote.py",
        """            lesson_root = worktree / lesson.publication.repository_path\n""",
        """            lesson_root = tex_file.parent.parent\n""",
    )
    replace_once(
        "src/tutor_assistant/latex/remote.py",
        """            self._rewrite_report_paths(compilation.report_file, worktree)\n            published_candidate = candidate.model_copy(deep=True)\n            published_candidate.latex.active_operation_id = None\n            published_candidate.latex.active_tex_blob_sha = None\n            published_candidate.latex.active_source_commit = None\n            published_candidate.latex.active_branch = None\n            published_candidate.latex.active_started_at = None\n            published_candidate.write_json(lesson_root / \"lesson.json\")\n            self._write_job_status(lesson_root, published_candidate, compilation)\n            run_git(worktree, \"add\", str(lesson_root.relative_to(worktree)))\n            status = \"success\" if compilation.success else \"failed\"\n            run_git(\n                worktree,\n                \"commit\",\n                \"-m\",\n                f\"Compile lesson PDF ({status}, attempt {candidate.latex.attempt})\",\n            )\n            commit = run_git(worktree, \"rev-parse\", \"HEAD\")\n            # No force: if the remote branch advanced after the probe, Git rejects the push.\n            run_git(\n                worktree,\n                \"push\",\n                self.repository.remote,\n                f\"HEAD:refs/heads/{probe.branch}\",\n            )\n            if cache_dir:\n                try:\n                    self._cache_result(compilation, cache_dir)\n                except OSError as exc:\n                    compilation.warnings.append(f\"Не удалось создать локальный кэш предпросмотра: {exc}\")\n            return RemoteCompilationResult(candidate, compilation, probe.branch, commit)\n""",
        """            self._rewrite_report_paths(compilation.report_file, worktree)\n            destination = (\n                cache_dir\n                or self.repo.parent\n                / \".tutor-assistant-latex-cache\"\n                / lesson.lesson_id\n            )\n            try:\n                self._cache_result(compilation, destination)\n            except OSError as exc:\n                raise RuntimeError(\n                    f\"Не удалось сохранить локальный результат LaTeX: {exc}\"\n                ) from exc\n            candidate.latex.tex_path = str(compilation.tex_file.resolve())\n            candidate.latex.report_path = str(compilation.report_file.resolve())\n            candidate.latex.preview_paths = [\n                str(path.resolve()) for path in compilation.preview_files\n            ]\n            candidate.latex.pdf_path = (\n                str(compilation.pdf_file.resolve())\n                if compilation.pdf_file\n                else None\n            )\n            candidate.latex.active_operation_id = None\n            candidate.latex.active_tex_blob_sha = None\n            candidate.latex.active_source_commit = None\n            candidate.latex.active_branch = None\n            candidate.latex.active_started_at = None\n            return RemoteCompilationResult(\n                candidate,\n                compilation,\n                probe.branch,\n                probe.remote_head,\n            )\n""",
    )
    (ROOT / "src/tutor_assistant/latex/__init__.py").write_text(
        """from .compiler import LatexCompiler\nfrom .diagnostics import inspect_latex_environment\nfrom .models import CompilationResult, EnvironmentReport\nfrom .remote import (\n    LatexCompilationReservation,\n    RemoteCompilationResult,\n    RemoteLatexService,\n    RemoteRepositoryUnavailable,\n    RemoteTexProbe,\n)\nfrom .validator import validate_tex\n\n__all__ = [\n    \"CompilationResult\",\n    \"EnvironmentReport\",\n    \"LatexCompilationReservation\",\n    \"LatexCompiler\",\n    \"RemoteCompilationResult\",\n    \"RemoteLatexService\",\n    \"RemoteRepositoryUnavailable\",\n    \"RemoteTexProbe\",\n    \"inspect_latex_environment\",\n    \"validate_tex\",\n]\n""",
        encoding="utf-8",
        newline="\n",
    )


def patch_publication_metadata() -> None:
    replace_once(
        "src/tutor_assistant/domain.py",
        """class PublicationInfo(BaseModel):\n    branch: str\n    repository_path: str\n    commit: str\n    pr_url: str | None = None\n    warnings: list[str] = Field(default_factory=list)\n""",
        """class PublicationInfo(BaseModel):\n    branch: str\n    repository_path: str\n    commit: str\n    operation_id: str | None = None\n    repository_full_name: str | None = None\n    remote_name: str = \"origin\"\n    previous_remote_commit: str | None = None\n    content_sha256: str | None = None\n    remote_verified: bool = False\n    idempotent: bool = False\n    published_at: datetime | None = None\n    pr_url: str | None = None\n    warnings: list[str] = Field(default_factory=list)\n""",
    )
    replace_once(
        "src/tutor_assistant/pipeline.py",
        """        current.publication = PublicationInfo(\n            branch=target.branch,\n            repository_path=target.repository_path,\n            commit=target.commit,\n            pr_url=target.pr_url,\n            warnings=list(target.warnings),\n        )\n""",
        """        current.publication = PublicationInfo(\n            branch=target.branch,\n            repository_path=target.repository_path,\n            commit=target.commit,\n            operation_id=target.operation_id,\n            repository_full_name=target.repository_full_name,\n            remote_name=target.remote_name,\n            previous_remote_commit=target.previous_remote_commit,\n            content_sha256=target.content_sha256,\n            remote_verified=target.remote_verified,\n            idempotent=target.idempotent,\n            published_at=target.published_at,\n            pr_url=target.pr_url,\n            warnings=list(target.warnings),\n        )\n""",
    )


def patch_example_config() -> None:
    path = ROOT / "config/app.example.yaml"
    text = path.read_text(encoding="utf-8")
    text = text.replace("  push: false\n", "  push: true\n", 1)
    text = text.replace("  auto_monitor: true\n", "  auto_monitor: false\n", 1)
    text = text.replace("  publish_pdf: true\n", "  publish_pdf: false\n", 1)
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    patch_publisher()
    patch_remote_latex()
    patch_publication_metadata()
    patch_example_config()


if __name__ == "__main__":
    main()
