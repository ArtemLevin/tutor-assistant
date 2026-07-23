from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import RepositoryConfig
from .domain import JobStatus, Lesson


class GitError(RuntimeError):
    pass


GIT_TIMEOUT_SECONDS = 120
GH_TIMEOUT_SECONDS = 30
PUBLICATION_ARTIFACTS = {
    "verified_transcript": "transcript.txt",
    "cleaned_transcript": "transcript_generated.txt",
    "timestamped_transcript": "transcript_timestamped.txt",
    "segments_json": "segments.json",
    "student_signals": "important_student_signals.json",
    "transcription_manifest": "transcription_manifest.json",
    "teacher_transcript": "teacher_transcript.txt",
    "student_transcript": "student_transcript.txt",
}


@dataclass(frozen=True)
class PublicationResult:
    branch: str
    repository_path: str
    commit: str
    pr_url: str | None = None
    warnings: tuple[str, ...] = ()


def _noninteractive_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
        }
    )
    return environment


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_noninteractive_environment(),
        )
    except FileNotFoundError as exc:
        raise GitError(f"Команда не найдена: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"Команда {command[0]} превысила timeout {timeout:g} секунд") from exc


def run_git(repo: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS) -> str:
    result = _run_command(["git", *args], cwd=repo, timeout=timeout)
    if result.returncode:
        raise GitError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _local_branch_exists(repo: Path, branch: str) -> bool:
    result = _run_command(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if result.returncode not in {0, 1}:
        raise GitError(result.stderr.strip() or result.stdout.strip())
    return result.returncode == 0


def _remote_branch_exists(repo: Path, remote: str, branch: str) -> bool:
    result = _run_command(
        ["git", "ls-remote", "--exit-code", "--heads", remote, branch],
        cwd=repo,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if result.returncode not in {0, 2}:
        raise GitError(result.stderr.strip() or result.stdout.strip())
    return result.returncode == 0


def ensure_private_repository(config: RepositoryConfig, checkout: Path) -> None:
    if not config.repository_full_name.strip():
        raise GitError("Укажите repository.repository_full_name перед публикацией")
    if shutil.which("gh") is None:
        raise GitError("Невозможно проверить приватность GitHub-репозитория: GitHub CLI не найден")
    result = _run_command(
        [
            "gh",
            "repo",
            "view",
            config.repository_full_name,
            "--json",
            "visibility",
            "--jq",
            ".visibility",
        ],
        cwd=checkout,
        timeout=GH_TIMEOUT_SECONDS,
    )
    if result.returncode:
        raise GitError(
            "Не удалось проверить приватность GitHub-репозитория: "
            + (result.stderr.strip() or result.stdout.strip())
        )
    visibility = result.stdout.strip().upper()
    if visibility != "PRIVATE":
        raise GitError(
            f"Публикация заблокирована: {config.repository_full_name} имеет visibility "
            f"{visibility or 'UNKNOWN'}, требуется PRIVATE"
        )


def publication_payload_files(lesson: Lesson) -> tuple[str, ...]:
    files = ["lesson.json", "job.status.json"]
    files.extend(
        f"source/{filename}"
        for field, filename in PUBLICATION_ARTIFACTS.items()
        if (value := getattr(lesson.artifacts, field)) and Path(value).is_file()
    )
    return tuple(files)


def create_draft_pr(
    config: RepositoryConfig, checkout: Path, lesson: Lesson, branch: str
) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    if not config.auto_create_pr:
        return None, warnings
    if shutil.which("gh") is None:
        return None, ["GitHub CLI не найден: draft PR нужно создать вручную"]
    auth = _run_command(
        ["gh", "auth", "status"],
        cwd=checkout,
        timeout=GH_TIMEOUT_SECONDS,
    )
    if auth.returncode:
        return None, ["GitHub CLI не авторизован: выполните gh auth login"]
    existing = _run_command(
        [
            "gh",
            "pr",
            "view",
            branch,
            "--repo",
            config.repository_full_name,
            "--json",
            "url",
            "--jq",
            ".url",
        ],
        cwd=checkout,
        timeout=GH_TIMEOUT_SECONDS,
    )
    if existing.returncode == 0 and existing.stdout.strip():
        return existing.stdout.strip(), warnings
    title = f"Lesson: {lesson.student.full_name} — {lesson.topic}"
    body = f"""## Занятие

- Ученик: {lesson.student.full_name}
- Дата: {lesson.lesson_date:%d.%m.%Y}
- Предмет: {lesson.subject}
- Тема: {lesson.topic}

## Конвейер

- [x] Подтверждённый транскрипт
- [ ] LaTeX-пособие
- [ ] PDF
- [ ] Образовательный плакат
- [ ] Web-эквивалент
- [ ] Проверка ссылок и index.html

PR создан Tutor Assistant и остаётся draft до завершения проверок.
"""
    result = _run_command(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--repo",
            config.repository_full_name,
            "--base",
            config.pr_base_branch,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=checkout,
        timeout=60,
    )
    if result.returncode:
        warnings.append("Не удалось создать draft PR: " + (result.stderr.strip() or result.stdout.strip()))
        return None, warnings
    return result.stdout.strip().splitlines()[-1], warnings


class LessonPublisher:
    def __init__(self, config: RepositoryConfig) -> None:
        self.config = config

    def _copy_job(self, lesson: Lesson, checkout: Path) -> Path:
        checkout = checkout.resolve()
        target = (checkout / lesson.student.folder / "lessons" / lesson.lesson_slug).resolve()
        if not target.is_relative_to(checkout):
            raise GitError("Папка ученика выходит за пределы Git-репозитория")
        source_dir = target / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        for field, filename in PUBLICATION_ARTIFACTS.items():
            value = getattr(lesson.artifacts, field)
            if value and Path(value).exists():
                shutil.copy2(value, source_dir / filename)
        lesson.transition(JobStatus.READY) if lesson.status == JobStatus.PUBLISHED else None
        lesson.write_json(target / "lesson.json")
        job_status = {
            "schema_version": "1.0",
            "lesson_id": lesson.lesson_id,
            "status": JobStatus.READY.value,
            "stage": "generation",
            "updated_at": datetime.now(UTC).isoformat(),
            "artifacts": {
                "tex": "pending",
                "pdf": "pending",
                "poster": "pending",
                "web": "pending",
                "index": "pending",
            },
        }
        (target / "job.status.json").write_text(
            json.dumps(job_status, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target

    def publish(self, lesson: Lesson, _lesson_dir: Path) -> PublicationResult:
        repo = self.config.students_repo.resolve()
        if not (repo / ".git").exists():
            raise GitError(f"Git-репозиторий не найден: {repo}")
        if self.config.push or self.config.auto_create_pr:
            ensure_private_repository(self.config, repo)
        run_git(repo, "fetch", self.config.remote, self.config.base_branch)
        branch = f"lesson/{lesson.student.id}-{lesson.lesson_date:%Y%m%d}-{lesson.lesson_id[:8]}"
        relative_target = Path(lesson.student.folder) / "lessons" / lesson.lesson_slug
        local_branch_exists = _local_branch_exists(repo, branch)
        if not local_branch_exists and _remote_branch_exists(
            repo,
            self.config.remote,
            branch,
        ):
            run_git(repo, "fetch", self.config.remote, branch)
            run_git(repo, "branch", branch, "FETCH_HEAD")
            local_branch_exists = True
        checkout = repo
        worktree_path: Path | None = None
        try:
            if self.config.use_worktree:
                root = repo.parent / ".tutor-assistant-worktrees"
                root.mkdir(parents=True, exist_ok=True)
                worktree_path = Path(tempfile.mkdtemp(prefix="lesson-", dir=root))
                worktree_path.rmdir()
                if local_branch_exists:
                    run_git(repo, "worktree", "add", str(worktree_path), branch)
                else:
                    run_git(
                        repo,
                        "worktree",
                        "add",
                        "-b",
                        branch,
                        str(worktree_path),
                        f"{self.config.remote}/{self.config.base_branch}",
                    )
                checkout = worktree_path
            elif self.config.create_branch:
                if run_git(repo, "status", "--porcelain"):
                    raise GitError("Основная копия содержит незакоммиченные изменения; включите use_worktree")
                run_git(repo, "switch", self.config.base_branch)
                run_git(repo, "pull", "--ff-only", self.config.remote, self.config.base_branch)
                if local_branch_exists:
                    run_git(repo, "switch", branch)
                else:
                    run_git(repo, "switch", "-c", branch)
            committed_target = (
                _run_command(
                    [
                        "git",
                        "cat-file",
                        "-e",
                        f"HEAD:{relative_target.as_posix()}/lesson.json",
                    ],
                    cwd=checkout,
                    timeout=GIT_TIMEOUT_SECONDS,
                ).returncode
                == 0
            )
            target = checkout / relative_target
            if not committed_target:
                target = self._copy_job(lesson, checkout)
                run_git(checkout, "add", str(target.relative_to(checkout)))
                run_git(
                    checkout,
                    "commit",
                    "-m",
                    f"Add lesson job for {lesson.student.full_name} ({lesson.lesson_date})",
                )
            commit = run_git(checkout, "rev-parse", "HEAD")
            if self.config.push:
                run_git(checkout, "push", "-u", self.config.remote, "HEAD")
            pr_url, warnings = create_draft_pr(self.config, checkout, lesson, branch)
            lesson.transition(JobStatus.PUBLISHED)
            return PublicationResult(
                branch, str(target.relative_to(checkout)), commit, pr_url, tuple(warnings)
            )
        finally:
            if worktree_path and worktree_path.exists() and not self.config.keep_worktree:
                try:
                    run_git(repo, "worktree", "remove", "--force", str(worktree_path))
                finally:
                    if worktree_path.exists():
                        shutil.rmtree(worktree_path, ignore_errors=True)
