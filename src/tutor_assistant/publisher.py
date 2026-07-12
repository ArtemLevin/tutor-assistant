from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import RepositoryConfig
from .domain import JobStatus, Lesson


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicationResult:
    branch: str
    repository_path: str
    commit: str


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if result.returncode:
        raise GitError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


class LessonPublisher:
    def __init__(self, config: RepositoryConfig) -> None:
        self.config = config

    def _copy_job(self, lesson: Lesson, checkout: Path) -> Path:
        target = checkout / lesson.student.folder / "lessons" / lesson.lesson_slug
        source_dir = target / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        mapping = {
            "verified_transcript": "transcript.txt",
            "cleaned_transcript": "transcript_generated.txt",
            "timestamped_transcript": "transcript_timestamped.txt",
            "segments_json": "segments.json",
            "student_signals": "important_student_signals.json",
            "transcription_manifest": "transcription_manifest.json",
        }
        for field, filename in mapping.items():
            value = getattr(lesson.artifacts, field)
            if value and Path(value).exists():
                shutil.copy2(value, source_dir / filename)
        lesson.transition(JobStatus.READY) if lesson.status == JobStatus.PUBLISHED else None
        lesson.write_json(target / "lesson.json")
        (target / "job.status.json").write_text(
            '{\n  "status": "ready_for_generation"\n}\n', encoding="utf-8"
        )
        return target

    def publish(self, lesson: Lesson, lesson_dir: Path) -> PublicationResult:
        repo = self.config.students_repo.resolve()
        if not (repo / ".git").exists():
            raise GitError(f"Git-репозиторий не найден: {repo}")
        run_git(repo, "fetch", self.config.remote, self.config.base_branch)
        branch = f"lesson/{lesson.student.id}-{lesson.lesson_date:%Y%m%d}-{lesson.lesson_id[:8]}"
        checkout = repo
        worktree_path: Path | None = None
        try:
            if self.config.use_worktree:
                root = repo.parent / ".tutor-assistant-worktrees"
                root.mkdir(parents=True, exist_ok=True)
                worktree_path = Path(tempfile.mkdtemp(prefix="lesson-", dir=root))
                worktree_path.rmdir()
                run_git(
                    repo, "worktree", "add", "-b", branch, str(worktree_path),
                    f"{self.config.remote}/{self.config.base_branch}",
                )
                checkout = worktree_path
            elif self.config.create_branch:
                if run_git(repo, "status", "--porcelain"):
                    raise GitError("Основная копия содержит незакоммиченные изменения; включите use_worktree")
                run_git(repo, "switch", self.config.base_branch)
                run_git(repo, "pull", "--ff-only", self.config.remote, self.config.base_branch)
                run_git(repo, "switch", "-c", branch)
            target = self._copy_job(lesson, checkout)
            run_git(checkout, "add", str(target.relative_to(checkout)))
            run_git(
                checkout, "commit", "-m",
                f"Add lesson job for {lesson.student.full_name} ({lesson.lesson_date})",
            )
            commit = run_git(checkout, "rev-parse", "HEAD")
            if self.config.push:
                run_git(checkout, "push", "-u", self.config.remote, "HEAD")
            lesson.transition(JobStatus.PUBLISHED)
            lesson.write_json(lesson_dir / "lesson.json")
            return PublicationResult(branch, str(target.relative_to(checkout)), commit)
        finally:
            if worktree_path and worktree_path.exists() and not self.config.keep_worktree:
                try:
                    run_git(repo, "worktree", "remove", "--force", str(worktree_path))
                finally:
                    if worktree_path.exists():
                        shutil.rmtree(worktree_path, ignore_errors=True)
