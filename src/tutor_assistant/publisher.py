from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .atomic_io import atomic_write_text
from .config import RepositoryConfig
from .domain import JobStatus, Lesson
from .github_api import GitHubApiError, GitHubRepositoryGateway, GitHubRestGateway


class GitError(RuntimeError):
    pass


GIT_TIMEOUT_SECONDS = 120
GH_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class PublicationPolicy:
    """Fail-closed contract for every file sent to the students repository."""

    allowed_filename: str = "transcript.txt"
    target_branch: str = "main"
    require_private_repository: bool = True


TRANSCRIPT_ONLY_POLICY = PublicationPolicy()


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
            encoding="utf-8",
            errors="replace",
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


def ensure_private_repository(
    config: RepositoryConfig,
    checkout: Path,
    gateway: GitHubRepositoryGateway | None = None,
) -> None:
    if not config.repository_full_name.strip():
        raise GitError("Укажите repository.repository_full_name перед публикацией")
    if shutil.which("gh") is None:
        try:
            (gateway or GitHubRestGateway(config)).ensure_private_repository()
        except GitHubApiError as exc:
            raise GitError(str(exc)) from exc
        return
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


def publication_repository_path(
    lesson: Lesson,
    policy: PublicationPolicy = TRANSCRIPT_ONLY_POLICY,
) -> PurePosixPath:
    path = PurePosixPath(lesson.student.folder) / "lessons" / lesson.lesson_slug / policy.allowed_filename
    if path.is_absolute() or ".." in path.parts or path.name != policy.allowed_filename:
        raise GitError("Путь публикации транскрипта выходит за разрешённые границы")
    return path


def publication_payload_files(lesson: Lesson) -> tuple[str, ...]:
    """Return the complete and exhaustive network egress payload."""

    return (publication_repository_path(lesson).as_posix(),)


def _verified_transcript(lesson: Lesson) -> tuple[Path, str]:
    if lesson.status != JobStatus.READY:
        raise GitError("Публикация разрешена только после подтверждения транскрипта")
    value = lesson.artifacts.verified_transcript
    if not value:
        raise GitError("Подтверждённый транскрипт отсутствует")
    source = Path(value)
    if not source.is_file():
        raise GitError(f"Подтверждённый транскрипт не найден: {source}")
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise GitError("Подтверждённый транскрипт должен быть UTF-8 текстом") from exc
    return source, text


def _git_paths(checkout: Path, *args: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in run_git(checkout, *args).splitlines() if line.strip())


def _assert_transcript_only_egress(paths: tuple[str, ...], expected: str) -> None:
    unexpected = tuple(path for path in paths if PurePosixPath(path).as_posix() != expected)
    if unexpected:
        details = ", ".join(unexpected)
        raise GitError(f"Публикация заблокирована: обнаружены посторонние файлы: {details}")
    if len(paths) > 1:
        raise GitError("Публикация заблокирована: разрешён ровно один transcript.txt")


class LessonPublisher:
    def __init__(
        self,
        config: RepositoryConfig,
        github_gateway: GitHubRepositoryGateway | None = None,
        policy: PublicationPolicy = TRANSCRIPT_ONLY_POLICY,
    ) -> None:
        self.config = config
        self.github_gateway = github_gateway
        self.policy = policy

    def _write_transcript(self, lesson: Lesson, checkout: Path, text: str) -> Path:
        relative = publication_repository_path(lesson, self.policy)
        checkout = checkout.resolve()
        target = (checkout / Path(relative.as_posix())).resolve()
        if not target.is_relative_to(checkout):
            raise GitError("Путь публикации транскрипта выходит за пределы Git-репозитория")
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, text)
        return target

    def publish(self, lesson: Lesson, _lesson_dir: Path) -> PublicationResult:
        del _lesson_dir  # Local lesson assets are deliberately outside the egress contract.
        _source, text = _verified_transcript(lesson)
        expected_path = publication_repository_path(lesson, self.policy).as_posix()
        payload = publication_payload_files(lesson)
        _assert_transcript_only_egress(payload, expected_path)
        if payload != (expected_path,):
            raise GitError("Публикация заблокирована: payload не соответствует transcript-only policy")

        repo = self.config.students_repo.resolve()
        if not (repo / ".git").exists():
            raise GitError(f"Git-репозиторий не найден: {repo}")
        if self.config.push and self.policy.require_private_repository:
            ensure_private_repository(self.config, repo, self.github_gateway)

        branch = self.policy.target_branch
        run_git(repo, "fetch", self.config.remote, branch)
        checkout = repo
        worktree_path: Path | None = None
        try:
            if self.config.use_worktree:
                root = repo.parent / ".tutor-assistant-worktrees"
                root.mkdir(parents=True, exist_ok=True)
                worktree_path = Path(tempfile.mkdtemp(prefix="transcript-", dir=root))
                worktree_path.rmdir()
                run_git(
                    repo,
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree_path),
                    f"{self.config.remote}/{branch}",
                )
                checkout = worktree_path
            else:
                if run_git(repo, "status", "--porcelain"):
                    raise GitError("Основная копия содержит незакоммиченные изменения; включите use_worktree")
                run_git(repo, "switch", branch)
                run_git(repo, "pull", "--ff-only", self.config.remote, branch)

            target = self._write_transcript(lesson, checkout, text)
            relative_target = target.relative_to(checkout).as_posix()
            if relative_target != expected_path:
                raise GitError("Публикация заблокирована: итоговый путь transcript.txt изменился")

            run_git(checkout, "add", "--", relative_target)
            staged = _git_paths(checkout, "diff", "--cached", "--name-only")
            _assert_transcript_only_egress(staged, expected_path)
            if staged:
                run_git(
                    checkout,
                    "commit",
                    "-m",
                    f"Publish transcript for {lesson.student.full_name} ({lesson.lesson_date})",
                )

            commit = run_git(checkout, "rev-parse", "HEAD")
            outgoing = _git_paths(
                checkout,
                "diff",
                "--name-only",
                f"{self.config.remote}/{branch}..HEAD",
            )
            _assert_transcript_only_egress(outgoing, expected_path)
            if self.config.push and outgoing:
                run_git(
                    checkout,
                    "push",
                    self.config.remote,
                    f"HEAD:refs/heads/{branch}",
                )
            lesson.transition(JobStatus.PUBLISHED)
            return PublicationResult(
                branch=branch,
                repository_path=expected_path,
                commit=commit,
                pr_url=None,
                warnings=(),
            )
        finally:
            if worktree_path and worktree_path.exists() and not self.config.keep_worktree:
                try:
                    run_git(repo, "worktree", "remove", "--force", str(worktree_path))
                finally:
                    if worktree_path.exists():
                        shutil.rmtree(worktree_path, ignore_errors=True)
