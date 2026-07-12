from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .config import RepositoryConfig
from .domain import JobStatus, Lesson


class GitError(RuntimeError):
    pass


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if result.returncode:
        raise GitError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


class LessonPublisher:
    def __init__(self, config: RepositoryConfig) -> None:
        self.config = config

    def publish(self, lesson: Lesson, lesson_dir: Path) -> Path:
        repo = self.config.students_repo.resolve()
        if not (repo / ".git").exists():
            raise GitError(f"Git-репозиторий не найден: {repo}")
        target = repo / lesson.student.folder / "lessons" / lesson.lesson_slug
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
        lesson.transition(JobStatus.READY)
        lesson.write_json(target / "lesson.json")
        (target / "job.status.json").write_text(
            '{\n  "status": "ready_for_generation"\n}\n', encoding="utf-8"
        )
        if self.config.create_branch:
            run_git(repo, "fetch", self.config.remote, self.config.base_branch)
            run_git(repo, "switch", self.config.base_branch)
            run_git(repo, "pull", "--ff-only", self.config.remote, self.config.base_branch)
            branch = f"lesson/{lesson.student.id}-{lesson.lesson_date:%Y%m%d}-{lesson.lesson_id[:8]}"
            run_git(repo, "switch", "-c", branch)
        run_git(repo, "add", str(target.relative_to(repo)))
        run_git(repo, "commit", "-m", f"Add lesson job for {lesson.student.full_name} ({lesson.lesson_date})")
        if self.config.push:
            run_git(repo, "push", "-u", self.config.remote, "HEAD")
        lesson.transition(JobStatus.PUBLISHED)
        lesson.write_json(lesson_dir / "lesson.json")
        return target

