from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from .atomic_io import atomic_write_text
from .config import RepositoryConfig
from .domain import JobStatus, Lesson
from .github_api import GitHubApiError, GitHubRepositoryGateway, GitHubRestGateway
from .publication import (
    PublicationOperation,
    PublicationOperationStatus,
    PublicationOperationStore,
    RemoteIdentityError,
    assert_expected_repository,
    describe_push_remote,
)


class GitError(RuntimeError):
    pass


class PublicationConflictError(GitError):
    pass


class PublicationIndeterminateError(GitError):
    pass


GIT_TIMEOUT_SECONDS = 120
GH_TIMEOUT_SECONDS = 30
_SHA1 = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class PublicationPolicy:
    """Fail-closed contract for every file sent to the students repository."""

    allowed_filename: str = "transcript.txt"
    target_branch: str = "main"
    require_private_repository: bool = True
    maximum_file_size_bytes: int = 2_000_000


TRANSCRIPT_ONLY_POLICY = PublicationPolicy()


@dataclass(frozen=True)
class PublicationPlan:
    lesson_id: str
    repository_full_name: str
    remote_name: str
    branch: str
    repository_path: str
    content_sha256: str
    content_size_bytes: int
    expected_remote_sha: str


@dataclass(frozen=True)
class PublicationResult:
    branch: str
    repository_path: str
    commit: str
    pr_url: str | None = None
    warnings: tuple[str, ...] = ()
    operation_id: str | None = None
    repository_full_name: str | None = None
    remote_name: str = "origin"
    previous_remote_commit: str | None = None
    content_sha256: str | None = None
    remote_verified: bool = False
    idempotent: bool = False
    published_at: datetime | None = None


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


def _run_git_text(repo: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS) -> str:
    result = _run_command(["git", *args], cwd=repo, timeout=timeout)
    if result.returncode:
        raise GitError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


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


def _draft_pr_copy(lesson: Lesson) -> tuple[str, str]:
    title = f"Lesson: {lesson.student.full_name} — {lesson.topic}"
    body = f"""## Занятие

- Ученик: {lesson.student.full_name}
- Дата: {lesson.lesson_date:%d.%m.%Y}
- Предмет: {lesson.subject}
- Тема: {lesson.topic}

PR создан Tutor Assistant и остаётся draft до завершения проверок.
"""
    return title, body


def create_draft_pr(
    config: RepositoryConfig,
    checkout: Path,
    lesson: Lesson,
    branch: str,
    gateway: GitHubRepositoryGateway | None = None,
) -> tuple[str | None, list[str]]:
    """Compatibility utility for legacy workflows that explicitly request a PR."""

    warnings: list[str] = []
    if not config.auto_create_pr:
        return None, warnings
    title, body = _draft_pr_copy(lesson)
    if shutil.which("gh") is None:
        try:
            api = gateway or GitHubRestGateway(config)
            existing = api.find_open_pull_request(branch, config.pr_base_branch)
            if existing:
                return existing, warnings
            return (
                api.create_draft_pull_request(
                    branch=branch,
                    base_branch=config.pr_base_branch,
                    title=title,
                    body=body,
                ),
                warnings,
            )
        except GitHubApiError as exc:
            warnings.append("Не удалось создать draft PR через GitHub API: " + str(exc))
            return None, warnings
    auth = _run_command(["gh", "auth", "status"], cwd=checkout, timeout=GH_TIMEOUT_SECONDS)
    if auth.returncode:
        return None, ["GitHub CLI не авторизован: выполните gh auth login"]
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


def publication_repository_path(
    lesson: Lesson,
    policy: PublicationPolicy = TRANSCRIPT_ONLY_POLICY,
) -> PurePosixPath:
    unique_slug = f"{lesson.lesson_slug}__{lesson.lesson_id[:8]}"
    path = PurePosixPath(lesson.student.folder) / "lessons" / unique_slug / policy.allowed_filename
    if path.is_absolute() or ".." in path.parts or path.name != policy.allowed_filename:
        raise GitError("Путь публикации транскрипта выходит за разрешённые границы")
    return path


def publication_payload_files(lesson: Lesson) -> tuple[str, ...]:
    """Return the complete and exhaustive network egress payload."""

    return (publication_repository_path(lesson).as_posix(),)


def _verified_transcript(lesson: Lesson, policy: PublicationPolicy) -> tuple[Path, str, str, int]:
    if lesson.status != JobStatus.READY:
        raise GitError("Публикация разрешена только после подтверждения транскрипта")
    value = lesson.artifacts.verified_transcript
    if not value:
        raise GitError("Подтверждённый транскрипт отсутствует")
    source = Path(value)
    if not source.is_file():
        raise GitError(f"Подтверждённый транскрипт не найден: {source}")
    payload = source.read_bytes()
    if len(payload) > policy.maximum_file_size_bytes:
        raise GitError(
            f"Подтверждённый транскрипт превышает {policy.maximum_file_size_bytes} байт"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GitError("Подтверждённый транскрипт должен быть UTF-8 текстом") from exc
    if text.startswith("\ufeff"):
        raise GitError("Подтверждённый транскрипт не должен содержать UTF-8 BOM")
    if "\x00" in text:
        raise GitError("Подтверждённый транскрипт содержит недопустимый NUL-символ")
    return source, text, hashlib.sha256(payload).hexdigest(), len(payload)


def _git_paths(checkout: Path, *args: str) -> tuple[str, ...]:
    output = run_git(checkout, "-c", "core.quotepath=false", *args)
    return tuple(line.strip() for line in output.splitlines() if line.strip())


def _assert_transcript_only_egress(paths: tuple[str, ...], expected: str) -> None:
    unexpected = tuple(path for path in paths if PurePosixPath(path).as_posix() != expected)
    if unexpected:
        details = ", ".join(unexpected)
        raise GitError(f"Публикация заблокирована: обнаружены посторонние файлы: {details}")
    if len(paths) > 1:
        raise GitError("Публикация заблокирована: разрешён ровно один transcript.txt")


def _remote_head(repo: Path, remote: str, branch: str) -> str:
    output = run_git(repo, "ls-remote", remote, f"refs/heads/{branch}")
    sha = output.split(maxsplit=1)[0] if output else ""
    if not _SHA1.fullmatch(sha):
        raise GitError(f"Не удалось определить SHA удалённой ветки {branch}")
    return sha


def _journal_path(lesson_dir: Path) -> Path:
    resolved = lesson_dir.resolve()
    workspace = resolved.parent.parent if resolved.parent.name == "lessons" else resolved
    return workspace / "publication.sqlite3"


def _result(
    operation: PublicationOperation,
    *,
    commit: str,
    idempotent: bool,
) -> PublicationResult:
    return PublicationResult(
        branch=operation.branch,
        repository_path=operation.repository_path,
        commit=commit,
        operation_id=operation.id,
        repository_full_name=operation.repository_full_name,
        remote_name=operation.remote_name,
        previous_remote_commit=operation.expected_remote_sha,
        content_sha256=operation.content_sha256,
        remote_verified=True,
        idempotent=idempotent,
        published_at=datetime.now(UTC),
    )


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

    def _descriptor(self, repo: Path):
        raw_url = run_git(repo, "remote", "get-url", "--push", self.config.remote)
        try:
            descriptor = describe_push_remote(self.config.remote, raw_url)
            assert_expected_repository(descriptor.identity, self.config.repository_full_name)
        except RemoteIdentityError as exc:
            raise GitError(str(exc)) from exc
        return descriptor

    def preview(self, lesson: Lesson, lesson_dir: Path) -> PublicationPlan:
        if not self.config.push:
            raise GitError(
                "Публикация отключена параметром repository.push=false. "
                "Production publish требует реальной отправки в remote."
            )
        _source, _text, content_sha256, size = _verified_transcript(lesson, self.policy)
        expected_path = publication_repository_path(lesson, self.policy).as_posix()
        _assert_transcript_only_egress(publication_payload_files(lesson), expected_path)
        repo = self.config.students_repo.resolve()
        if not (repo / ".git").exists():
            raise GitError(f"Git-репозиторий не найден: {repo}")
        descriptor = self._descriptor(repo)
        if self.policy.require_private_repository:
            ensure_private_repository(self.config, repo, self.github_gateway)
        run_git(repo, "fetch", self.config.remote, self.policy.target_branch)
        expected_remote_sha = run_git(
            repo,
            "rev-parse",
            f"{self.config.remote}/{self.policy.target_branch}",
        )
        if not _SHA1.fullmatch(expected_remote_sha):
            raise GitError("Git fetch не вернул корректный SHA целевой ветки")
        del lesson_dir
        return PublicationPlan(
            lesson_id=lesson.lesson_id,
            repository_full_name=descriptor.identity.full_name,
            remote_name=descriptor.remote_name,
            branch=self.policy.target_branch,
            repository_path=expected_path,
            content_sha256=content_sha256,
            content_size_bytes=size,
            expected_remote_sha=expected_remote_sha,
        )

    def _reconcile_active(
        self,
        lesson: Lesson,
        store: PublicationOperationStore,
        repo: Path,
    ) -> PublicationResult | None:
        operation = store.active_for_lesson(lesson.lesson_id)
        if operation is None:
            return None
        remote_sha = _remote_head(repo, operation.remote_name, operation.branch)
        if operation.status == PublicationOperationStatus.REMOTE_VERIFIED:
            if remote_sha != operation.remote_commit_sha:
                store.mark_conflict(
                    operation.id,
                    remote_commit_sha=remote_sha,
                    details="Remote изменился после подтверждения publication operation",
                )
                raise PublicationConflictError("Удалённая ветка изменилась после публикации")
            completed = store.mark_completed(operation.id)
            lesson.transition(JobStatus.PUBLISHED)
            return _result(completed, commit=remote_sha, idempotent=True)
        if operation.local_commit_sha and remote_sha == operation.local_commit_sha:
            verified = store.mark_remote_verified(operation.id, remote_sha)
            completed = store.mark_completed(verified.id)
            lesson.transition(JobStatus.PUBLISHED)
            return _result(completed, commit=remote_sha, idempotent=True)
        if remote_sha == operation.expected_remote_sha:
            store.mark_failed(
                operation.id,
                error_code="push_not_applied",
                details="Remote SHA не изменился; operation безопасно завершена как failed",
            )
            return None
        store.mark_conflict(
            operation.id,
            remote_commit_sha=remote_sha,
            details="Remote SHA не совпадает с expected или local publication commit",
        )
        raise PublicationConflictError(
            "Удалённая ветка main изменилась во время публикации; повторите операцию"
        )

    def publish(self, lesson: Lesson, lesson_dir: Path) -> PublicationResult:
        plan = self.preview(lesson, lesson_dir)
        _source, text, _sha256, _size = _verified_transcript(lesson, self.policy)
        repo = self.config.students_repo.resolve()
        store = PublicationOperationStore(_journal_path(lesson_dir))
        reconciled = self._reconcile_active(lesson, store, repo)
        if reconciled is not None:
            return reconciled

        operation = store.begin(
            lesson_id=lesson.lesson_id,
            repository_full_name=plan.repository_full_name,
            remote_name=plan.remote_name,
            remote_url_sha256=self._descriptor(repo).url_sha256,
            branch=plan.branch,
            repository_path=plan.repository_path,
            content_sha256=plan.content_sha256,
            content_size_bytes=plan.content_size_bytes,
            expected_remote_sha=plan.expected_remote_sha,
        )

        try:
            existing = _run_git_text(
                repo,
                "show",
                f"{self.config.remote}/{plan.branch}:{plan.repository_path}",
            )
        except GitError:
            existing = None
        if existing is not None and hashlib.sha256(existing.encode("utf-8")).hexdigest() == plan.content_sha256:
            verified = store.mark_remote_verified(
                operation.id,
                plan.expected_remote_sha,
                allow_prepared=True,
            )
            completed = store.mark_completed(verified.id)
            lesson.transition(JobStatus.PUBLISHED)
            return _result(completed, commit=plan.expected_remote_sha, idempotent=True)

        root = repo.parent / ".tutor-assistant-worktrees"
        root.mkdir(parents=True, exist_ok=True)
        worktree_path = Path(tempfile.mkdtemp(prefix="transcript-", dir=root))
        worktree_path.rmdir()
        local_commit: str | None = None
        try:
            run_git(repo, "worktree", "add", "--detach", str(worktree_path), plan.expected_remote_sha)
            target = self._write_transcript(lesson, worktree_path, text)
            relative_target = target.relative_to(worktree_path).as_posix()
            if relative_target != plan.repository_path:
                raise GitError("Публикация заблокирована: итоговый путь transcript.txt изменился")
            run_git(worktree_path, "add", "--", relative_target)
            staged = _git_paths(worktree_path, "diff", "--cached", "--name-only")
            _assert_transcript_only_egress(staged, plan.repository_path)
            if not staged:
                raise GitError("Git не обнаружил изменений для публикации")
            run_git(
                worktree_path,
                "commit",
                "-m",
                f"Publish transcript for {lesson.student.full_name} ({lesson.lesson_date})",
            )
            local_commit = run_git(worktree_path, "rev-parse", "HEAD")
            parent = run_git(worktree_path, "rev-parse", "HEAD^")
            if parent != plan.expected_remote_sha:
                raise GitError("Publication commit построен не от зафиксированного remote SHA")
            changed = _git_paths(
                worktree_path,
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "HEAD",
            )
            _assert_transcript_only_egress(changed, plan.repository_path)
            outgoing = _git_paths(
                worktree_path,
                "diff",
                "--name-only",
                f"{plan.expected_remote_sha}..HEAD",
            )
            _assert_transcript_only_egress(outgoing, plan.repository_path)
            operation = store.mark_pushing(operation.id, local_commit)
            run_git(
                worktree_path,
                "push",
                "--porcelain",
                self.config.remote,
                f"HEAD:refs/heads/{plan.branch}",
            )
            remote_sha = _remote_head(repo, self.config.remote, plan.branch)
            if remote_sha != local_commit:
                store.mark_indeterminate(
                    operation.id,
                    error_code="verification_failed",
                    details=f"Remote SHA {remote_sha} не совпал с local commit {local_commit}",
                )
                raise PublicationIndeterminateError(
                    "Git push завершён, однако remote commit не подтверждён"
                )
            verified = store.mark_remote_verified(operation.id, remote_sha)
            completed = store.mark_completed(verified.id)
            lesson.transition(JobStatus.PUBLISHED)
            return _result(completed, commit=remote_sha, idempotent=False)
        except Exception as exc:
            current = store.get(operation.id)
            if current.status == PublicationOperationStatus.PUSHING and local_commit:
                try:
                    remote_sha = _remote_head(repo, self.config.remote, plan.branch)
                except Exception:
                    store.mark_indeterminate(
                        operation.id,
                        error_code="remote_unavailable",
                        details=str(exc),
                    )
                    raise PublicationIndeterminateError(
                        "Результат публикации неизвестен; требуется reconciliation"
                    ) from exc
                if remote_sha == local_commit:
                    verified = store.mark_remote_verified(operation.id, remote_sha)
                    completed = store.mark_completed(verified.id)
                    lesson.transition(JobStatus.PUBLISHED)
                    return _result(completed, commit=remote_sha, idempotent=True)
                if remote_sha == plan.expected_remote_sha:
                    store.mark_failed(
                        operation.id,
                        error_code="push_failed",
                        details=str(exc),
                    )
                else:
                    store.mark_conflict(
                        operation.id,
                        remote_commit_sha=remote_sha,
                        details=str(exc),
                    )
            elif current.status == PublicationOperationStatus.PREPARED:
                store.mark_failed(
                    operation.id,
                    error_code="prepare_failed",
                    details=str(exc),
                )
            raise
        finally:
            if worktree_path.exists():
                try:
                    run_git(repo, "worktree", "remove", "--force", str(worktree_path))
                finally:
                    if worktree_path.exists():
                        shutil.rmtree(worktree_path, ignore_errors=True)
