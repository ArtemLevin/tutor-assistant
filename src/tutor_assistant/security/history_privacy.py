from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence


class HistoryPrivacyError(RuntimeError):
    """Raised when a privacy-history operation cannot continue safely."""


REWRITE_CONFIRMATION = "REWRITE_TUTOR_ASSISTANT_HISTORY"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class HistoryPrivacyPolicy:
    schema_version: str
    repository_full_name: str
    baseline_commit: str
    forbidden_paths: tuple[str, ...]
    rewrite_remove_paths: tuple[str, ...]
    required_visibility: str = "PRIVATE"

    @classmethod
    def load(cls, path: Path) -> HistoryPrivacyPolicy:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HistoryPrivacyError(f"Не удалось прочитать privacy policy: {path}") from exc
        try:
            policy = cls(
                schema_version=str(payload["schema_version"]),
                repository_full_name=str(payload["repository_full_name"]),
                baseline_commit=str(payload["baseline_commit"]),
                forbidden_paths=tuple(str(value) for value in payload["forbidden_paths"]),
                rewrite_remove_paths=tuple(str(value) for value in payload["rewrite_remove_paths"]),
                required_visibility=str(payload.get("required_visibility", "PRIVATE")).upper(),
            )
        except (KeyError, TypeError) as exc:
            raise HistoryPrivacyError("Privacy policy имеет неполную структуру") from exc
        policy.validate()
        return policy

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise HistoryPrivacyError(f"Неподдерживаемая версия privacy policy: {self.schema_version}")
        if not self.repository_full_name or "/" not in self.repository_full_name:
            raise HistoryPrivacyError("repository_full_name должен иметь формат owner/repository")
        if len(self.baseline_commit) < 7:
            raise HistoryPrivacyError("baseline_commit отсутствует или слишком короткий")
        if not self.forbidden_paths or not self.rewrite_remove_paths:
            raise HistoryPrivacyError("Списки forbidden_paths и rewrite_remove_paths должны быть заполнены")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "repository_full_name": self.repository_full_name,
            "baseline_commit": self.baseline_commit,
            "required_visibility": self.required_visibility,
            "forbidden_paths": list(self.forbidden_paths),
            "rewrite_remove_paths": list(self.rewrite_remove_paths),
        }


@dataclass
class PrivacyAuditReport:
    mode: str
    repository_root: str
    head: str | None
    baseline_commit: str
    visibility: str | None = None
    checked_path_count: int = 0
    findings: list[str] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def passed(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class RewriteArtifacts:
    mirror: Path
    backup_bundle: Path
    refs_manifest: Path
    collaborator_notice: Path
    audit_report: Path
    rewritten_policy: Path
    post_clone_report: Path | None = None


def _command_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"})
    if extra:
        environment.update(extra)
    return environment


def _run_process(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    check: bool = True,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_command_environment(environment),
        )
    except FileNotFoundError as exc:
        raise HistoryPrivacyError(f"Команда не найдена: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HistoryPrivacyError(f"Команда превысила timeout {timeout:g} секунд: {command[0]}") from exc
    if check and result.returncode:
        details = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise HistoryPrivacyError(f"Команда завершилась с ошибкой: {' '.join(command)}\n{details}")
    return result


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run_process(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=repo,
        check=check,
    )


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").strip().strip("/")


def path_is_forbidden(path: str, forbidden_paths: Iterable[str]) -> bool:
    normalized = _normalize_path(path)
    for candidate in forbidden_paths:
        forbidden = _normalize_path(candidate)
        if normalized == forbidden or normalized.startswith(forbidden + "/"):
            return True
    return False


def _nul_paths(output: str) -> tuple[str, ...]:
    return tuple(value for value in output.split("\0") if value)


def _object_paths(output: str) -> tuple[str, ...]:
    paths: list[str] = []
    for line in output.splitlines():
        _object_sha, separator, path = line.partition(" ")
        if separator and path:
            paths.append(path)
    return tuple(paths)


def _repository_root(repo: Path) -> Path:
    root = _git(repo, "rev-parse", "--show-toplevel", check=False)
    if root.returncode == 0 and root.stdout.strip():
        return Path(root.stdout.strip()).resolve()
    bare = _git(repo, "rev-parse", "--is-bare-repository", check=False)
    if bare.returncode == 0 and bare.stdout.strip().casefold() == "true":
        return repo.resolve()
    raise HistoryPrivacyError(f"Git-репозиторий не найден: {repo}")


def _head_sha(repo: Path) -> str | None:
    result = _git(repo, "rev-parse", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _visibility(policy: HistoryPrivacyPolicy, repo: Path) -> str:
    result = _run_process(
        [
            "gh",
            "repo",
            "view",
            policy.repository_full_name,
            "--json",
            "visibility",
            "--jq",
            ".visibility",
        ],
        cwd=repo,
        check=False,
        timeout=30,
    )
    if result.returncode:
        details = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise HistoryPrivacyError(f"Не удалось проверить visibility GitHub-репозитория: {details}")
    return result.stdout.strip().upper()


def _current_tree_paths(repo: Path) -> tuple[str, ...]:
    head = _head_sha(repo)
    if not head:
        return ()
    return _nul_paths(_git(repo, "ls-tree", "-r", "--name-only", "-z", head).stdout)


def _range_object_paths(repo: Path, baseline: str) -> tuple[str, ...]:
    return _object_paths(_git(repo, "rev-list", "--objects", f"{baseline}..HEAD").stdout)


def audit_repository(
    policy: HistoryPrivacyPolicy,
    repo: Path,
    *,
    mode: str = "head",
    require_visibility: bool = False,
) -> PrivacyAuditReport:
    if mode not in {"head", "full"}:
        raise HistoryPrivacyError("Audit mode должен быть head или full")
    root = _repository_root(repo)
    report = PrivacyAuditReport(
        mode=mode,
        repository_root=str(root),
        head=_head_sha(root),
        baseline_commit=policy.baseline_commit,
    )

    current_paths = _current_tree_paths(root)
    report.checked_path_count += len(current_paths)
    for path in current_paths:
        if path_is_forbidden(path, policy.forbidden_paths):
            report.findings.append(f"forbidden_tracked_path:{path}")

    if mode == "head":
        baseline_exists = _git(root, "cat-file", "-e", f"{policy.baseline_commit}^{{commit}}", check=False)
        if baseline_exists.returncode:
            report.findings.append(f"missing_baseline_commit:{policy.baseline_commit}")
        else:
            ancestor = _git(
                root,
                "merge-base",
                "--is-ancestor",
                policy.baseline_commit,
                "HEAD",
                check=False,
            )
            if ancestor.returncode:
                report.findings.append(f"baseline_not_ancestor:{policy.baseline_commit}")
            else:
                changed_history = _range_object_paths(root, policy.baseline_commit)
                report.checked_path_count += len(changed_history)
                for path in changed_history:
                    if path_is_forbidden(path, policy.forbidden_paths):
                        report.findings.append(f"forbidden_path_since_baseline:{path}")

    if mode == "full":
        historical = _object_paths(_git(root, "rev-list", "--objects", "--all").stdout)
        report.checked_path_count += len(historical)
        for path in historical:
            if path_is_forbidden(path, policy.forbidden_paths):
                report.findings.append(f"forbidden_historical_path:{path}")

    if require_visibility or mode == "full":
        try:
            report.visibility = _visibility(policy, root)
        except HistoryPrivacyError as exc:
            report.findings.append(f"visibility_check_failed:{exc}")
        else:
            if report.visibility != policy.required_visibility:
                report.findings.append(
                    f"invalid_visibility:{report.visibility or 'UNKNOWN'}:required={policy.required_visibility}"
                )

    report.findings = sorted(set(report.findings))
    return report


def build_filter_repo_command(policy: HistoryPrivacyPolicy) -> tuple[str, ...]:
    command: list[str] = ["git", "filter-repo", "--force", "--invert-paths"]
    for path in policy.rewrite_remove_paths:
        command.extend(("--path", _normalize_path(path)))
    return tuple(command)


def validate_rewrite_confirmation(value: str | None) -> None:
    if value != REWRITE_CONFIRMATION:
        raise HistoryPrivacyError(
            "Force-push заблокирован. Передайте точную фразу подтверждения: " + REWRITE_CONFIRMATION
        )


def _collect_refs(mirror: Path) -> dict[str, str]:
    output = _git(
        mirror,
        "for-each-ref",
        "--format=%(refname) %(objectname)",
        "refs/heads",
        "refs/tags",
    ).stdout
    refs: dict[str, str] = {}
    for line in output.splitlines():
        ref, separator, sha = line.partition(" ")
        if separator and ref and sha:
            refs[ref] = sha.strip()
    return refs


def _write_collaborator_notice(path: Path, policy: HistoryPrivacyPolicy, refs: dict[str, str]) -> None:
    lines = [
        "# Tutor Assistant history rewrite notice",
        "",
        f"Repository: `{policy.repository_full_name}`",
        f"Generated: `{datetime.now(UTC).isoformat()}`",
        "",
        "Git history was rewritten to remove tracked runtime privacy files.",
        "Every existing clone must be archived and cloned again after the force-push.",
        "Local branches based on previous SHAs must not be pushed back to the remote.",
        "",
        "## Pre-rewrite refs",
        "",
    ]
    lines.extend(f"- `{ref}`: `{sha}`" for ref, sha in sorted(refs.items()))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _refresh_policy_baseline(
    mirror: Path,
    policy: HistoryPrivacyPolicy,
    *,
    output_policy: Path,
) -> HistoryPrivacyPolicy:
    main_ref = "refs/heads/main"
    baseline = _git(mirror, "rev-parse", main_ref).stdout.strip()
    rewritten_policy = replace(policy, baseline_commit=baseline)
    rewritten_policy.validate()

    worktree_root = mirror.parent / "policy-worktree"
    _git(mirror, "worktree", "add", "--detach", str(worktree_root), main_ref)
    try:
        policy_path = worktree_root / "policy" / "privacy-history.json"
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_text(
            json.dumps(rewritten_policy.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _git(worktree_root, "add", "--", "policy/privacy-history.json")
        _git(
            worktree_root,
            "-c",
            "user.name=Tutor Assistant Privacy Rewrite",
            "-c",
            "user.email=privacy-rewrite@users.noreply.github.com",
            "commit",
            "-m",
            "Refresh privacy history baseline after rewrite",
        )
        new_head = _git(worktree_root, "rev-parse", "HEAD").stdout.strip()
        _git(mirror, "update-ref", main_ref, new_head, baseline)
    finally:
        _git(mirror, "worktree", "remove", "--force", str(worktree_root), check=False)

    output_policy.write_text(
        json.dumps(rewritten_policy.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return rewritten_policy


def _push_rewritten_refs(mirror: Path, remote_url: str, old_refs: dict[str, str]) -> None:
    remotes = _git(mirror, "remote", check=False).stdout.split()
    if "origin" not in remotes:
        _git(mirror, "remote", "add", "origin", remote_url)
    for ref, old_sha in sorted(old_refs.items()):
        exists = _git(mirror, "show-ref", "--verify", "--quiet", ref, check=False)
        refspec = f"{ref}:{ref}" if exists.returncode == 0 else f":{ref}"
        _git(
            mirror,
            "push",
            "origin",
            f"--force-with-lease={ref}:{old_sha}",
            refspec,
        )


def rewrite_history(
    policy: HistoryPrivacyPolicy,
    *,
    repository_url: str,
    output_dir: Path,
    execute: bool,
    force_push: bool,
    confirmation: str | None,
) -> RewriteArtifacts:
    output_dir = output_dir.resolve()
    mirror = output_dir / "rewritten-mirror.git"
    bundle = output_dir / "pre-rewrite-backup.bundle"
    refs_manifest = output_dir / "pre-rewrite-refs.json"
    notice = output_dir / "COLLABORATOR_NOTICE.md"
    audit_path = output_dir / "rewritten-history-audit.json"
    rewritten_policy_path = output_dir / "post-rewrite-privacy-history.json"
    post_clone_path = output_dir / "post-rewrite-clone-audit.json"

    if not execute:
        raise HistoryPrivacyError("Rewrite работает только с явным флагом --execute")
    if force_push:
        validate_rewrite_confirmation(confirmation)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise HistoryPrivacyError(f"Каталог результатов должен быть пустым: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    visibility = _visibility(policy, output_dir)
    if visibility != policy.required_visibility:
        raise HistoryPrivacyError(
            f"History rewrite заблокирован: visibility={visibility or 'UNKNOWN'}, "
            f"требуется {policy.required_visibility}"
        )

    filter_repo_probe = _run_process(
        ["git", "filter-repo", "--version"],
        cwd=output_dir,
        check=False,
        timeout=30,
    )
    if filter_repo_probe.returncode:
        raise HistoryPrivacyError("Установите git-filter-repo перед history rewrite")

    _run_process(["git", "clone", "--mirror", repository_url, str(mirror)], cwd=output_dir)
    old_refs = _collect_refs(mirror)
    if not old_refs:
        raise HistoryPrivacyError("В mirror clone отсутствуют branches и tags")

    _git(mirror, "bundle", "create", str(bundle), "--all")
    _git(mirror, "bundle", "verify", str(bundle))
    refs_manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "repository": policy.repository_full_name,
                "created_at": datetime.now(UTC).isoformat(),
                "refs": old_refs,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_collaborator_notice(notice, policy, old_refs)

    _run_process(build_filter_repo_command(policy), cwd=mirror, timeout=1800)
    active_policy = _refresh_policy_baseline(
        mirror,
        policy,
        output_policy=rewritten_policy_path,
    )
    rewritten_report = audit_repository(active_policy, mirror, mode="full", require_visibility=False)
    rewritten_report.visibility = visibility
    rewritten_report.findings = [
        finding for finding in rewritten_report.findings if not finding.startswith("visibility_")
    ]
    rewritten_report.write(audit_path)
    if not rewritten_report.passed:
        raise HistoryPrivacyError(
            "Локальная rewrite-проверка обнаружила нарушения: " + ", ".join(rewritten_report.findings)
        )

    final_post_clone: Path | None = None
    if force_push:
        _push_rewritten_refs(mirror, repository_url, old_refs)
        with tempfile.TemporaryDirectory(prefix="tutor-assistant-post-rewrite-") as temporary:
            clone = Path(temporary) / "verification.git"
            _run_process(["git", "clone", "--mirror", repository_url, str(clone)], cwd=output_dir)
            post_report = audit_repository(active_policy, clone, mode="full", require_visibility=True)
            post_report.write(post_clone_path)
            if not post_report.passed:
                raise HistoryPrivacyError(
                    "Post-rewrite clone verification завершилась с нарушениями: "
                    + ", ".join(post_report.findings)
                )
        final_post_clone = post_clone_path

    return RewriteArtifacts(
        mirror=mirror,
        backup_bundle=bundle,
        refs_manifest=refs_manifest,
        collaborator_notice=notice,
        audit_report=audit_path,
        rewritten_policy=rewritten_policy_path,
        post_clone_report=final_post_clone,
    )


def _default_policy_path() -> Path:
    return Path(__file__).resolve().parents[3] / "policy" / "privacy-history.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tutor Assistant historical privacy policy gate")
    parser.add_argument("--policy", type=Path, default=_default_policy_path())
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="Audit current or complete Git history")
    audit.add_argument("--repo", type=Path, default=Path.cwd())
    audit.add_argument("--mode", choices=("head", "full"), default="head")
    audit.add_argument("--require-visibility", action="store_true")
    audit.add_argument("--report", type=Path, default=Path("privacy-history-report.json"))

    plan = subparsers.add_parser("plan", help="Print the exact git-filter-repo command")
    plan.add_argument("--json", action="store_true", dest="as_json")

    rewrite = subparsers.add_parser("rewrite", help="Create a verified rewritten mirror")
    rewrite.add_argument("--repository-url", required=True)
    rewrite.add_argument("--output-dir", type=Path, required=True)
    rewrite.add_argument("--execute", action="store_true")
    rewrite.add_argument("--force-push", action="store_true")
    rewrite.add_argument("--confirm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        policy = HistoryPrivacyPolicy.load(args.policy)
        if args.command == "audit":
            report = audit_repository(
                policy,
                args.repo,
                mode=args.mode,
                require_visibility=args.require_visibility,
            )
            report.write(args.report)
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
            return 0 if report.passed else 1
        if args.command == "plan":
            command = build_filter_repo_command(policy)
            if args.as_json:
                print(json.dumps({"command": list(command)}, ensure_ascii=False, indent=2))
            else:
                print(" ".join(command))
            return 0
        artifacts = rewrite_history(
            policy,
            repository_url=args.repository_url,
            output_dir=args.output_dir,
            execute=args.execute,
            force_push=args.force_push,
            confirmation=args.confirm,
        )
        print(json.dumps({key: str(value) if value else None for key, value in asdict(artifacts).items()}, indent=2))
        return 0
    except HistoryPrivacyError as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
