from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tutor_assistant.security import history_privacy
from tutor_assistant.security.history_privacy import (
    REWRITE_CONFIRMATION,
    HistoryPrivacyError,
    HistoryPrivacyPolicy,
    audit_repository,
    build_filter_repo_command,
    path_is_forbidden,
    rewrite_history,
    validate_rewrite_confirmation,
)


def _run(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _run(repo, "add", "-A")
    _run(repo, "commit", "-m", message)
    return _run(repo, "rev-parse", "HEAD")


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-b", "main")
    _run(repo, "config", "user.name", "Privacy Tests")
    _run(repo, "config", "user.email", "privacy-tests@example.invalid")
    (repo / "README.md").write_text("safe\n", encoding="utf-8")
    baseline = _commit(repo, "Initial safe tree")
    return repo, baseline


def _policy(baseline: str) -> HistoryPrivacyPolicy:
    return HistoryPrivacyPolicy(
        schema_version="1.0",
        repository_full_name="owner/tutor-assistant",
        baseline_commit=baseline,
        required_visibility="PRIVATE",
        forbidden_paths=(".env", "config/app.yaml", "config/students.yaml", "data"),
        rewrite_remove_paths=(".env", "config/app.yaml", "config/students.yaml", "data"),
    )


def test_policy_loads_and_normalizes_filter_repo_command(tmp_path: Path) -> None:
    policy_path = tmp_path / "privacy-history.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "repository_full_name": "owner/tutor-assistant",
                "baseline_commit": "abcdef0123456789",
                "required_visibility": "private",
                "forbidden_paths": ["config/app.yaml", "data"],
                "rewrite_remove_paths": ["config\\app.yaml", "data/"],
            }
        ),
        encoding="utf-8",
    )

    policy = HistoryPrivacyPolicy.load(policy_path)

    assert policy.required_visibility == "PRIVATE"
    assert build_filter_repo_command(policy) == (
        "git",
        "filter-repo",
        "--force",
        "--invert-paths",
        "--path",
        "config/app.yaml",
        "--path",
        "data",
    )


def test_path_policy_matches_exact_paths_and_descendants() -> None:
    forbidden = ("config/app.yaml", "data")

    assert path_is_forbidden("config/app.yaml", forbidden)
    assert path_is_forbidden("data/lessons/example/transcript.txt", forbidden)
    assert path_is_forbidden("data\\lessons\\example.wav", forbidden)
    assert not path_is_forbidden("config/app.example.yaml", forbidden)
    assert not path_is_forbidden("database/schema.sql", forbidden)


def test_head_audit_accepts_safe_history_after_baseline(tmp_path: Path) -> None:
    repo, baseline = _repository(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "safe.py").write_text("VALUE = 1\n", encoding="utf-8")
    _commit(repo, "Add safe source")

    report = audit_repository(_policy(baseline), repo, mode="head")

    assert report.passed
    assert report.findings == []
    assert report.checked_path_count >= 2


def test_head_audit_detects_forbidden_path_added_then_deleted(tmp_path: Path) -> None:
    repo, baseline = _repository(tmp_path)
    config = repo / "config"
    config.mkdir()
    runtime = config / "students.yaml"
    runtime.write_text("students:\n  - full_name: Example Student\n", encoding="utf-8")
    _commit(repo, "Accidentally add runtime student configuration")
    runtime.unlink()
    config.rmdir()
    _commit(repo, "Remove runtime student configuration")

    report = audit_repository(_policy(baseline), repo, mode="head")

    assert not report.passed
    assert "forbidden_path_since_baseline:config/students.yaml" in report.findings


def test_head_audit_rejects_missing_or_unrelated_baseline(tmp_path: Path) -> None:
    repo, _baseline = _repository(tmp_path)
    missing_policy = _policy("1234567890abcdef")

    missing_report = audit_repository(missing_policy, repo, mode="head")

    assert not missing_report.passed
    assert "missing_baseline_commit:1234567890abcdef" in missing_report.findings

    other = tmp_path / "other"
    other.mkdir()
    _run(other, "init", "-b", "main")
    _run(other, "config", "user.name", "Privacy Tests")
    _run(other, "config", "user.email", "privacy-tests@example.invalid")
    (other / "other.txt").write_text("other\n", encoding="utf-8")
    unrelated = _commit(other, "Unrelated root")
    _run(repo, "fetch", str(other), f"{unrelated}:refs/other/unrelated")

    unrelated_report = audit_repository(_policy(unrelated), repo, mode="head")

    assert not unrelated_report.passed
    assert f"baseline_not_ancestor:{unrelated}" in unrelated_report.findings


def test_full_audit_detects_historical_forbidden_path_and_visibility(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo, baseline = _repository(tmp_path)
    secret = repo / ".env"
    secret.write_text("API_KEY=synthetic-test-value\n", encoding="utf-8")
    _commit(repo, "Add forbidden historical path")
    secret.unlink()
    _commit(repo, "Remove forbidden historical path")
    monkeypatch.setattr(history_privacy, "_visibility", lambda *_args: "PUBLIC")

    report = audit_repository(_policy(baseline), repo, mode="full")

    assert not report.passed
    assert "forbidden_historical_path:.env" in report.findings
    assert "invalid_visibility:PUBLIC:required=PRIVATE" in report.findings


def test_full_audit_accepts_clean_private_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo, baseline = _repository(tmp_path)
    monkeypatch.setattr(history_privacy, "_visibility", lambda *_args: "PRIVATE")

    report = audit_repository(_policy(baseline), repo, mode="full")

    assert report.passed
    assert report.visibility == "PRIVATE"


def test_force_push_confirmation_is_exact() -> None:
    validate_rewrite_confirmation(REWRITE_CONFIRMATION)

    with pytest.raises(HistoryPrivacyError, match=REWRITE_CONFIRMATION):
        validate_rewrite_confirmation(None)
    with pytest.raises(HistoryPrivacyError, match=REWRITE_CONFIRMATION):
        validate_rewrite_confirmation(REWRITE_CONFIRMATION.lower())


def test_rewrite_requires_execute_before_external_commands(tmp_path: Path) -> None:
    policy = _policy("abcdef0123456789")

    with pytest.raises(HistoryPrivacyError, match="--execute"):
        rewrite_history(
            policy,
            repository_url="https://example.invalid/owner/repo.git",
            output_dir=tmp_path / "rewrite",
            execute=False,
            force_push=False,
            confirmation=None,
        )


def test_rewrite_blocks_public_repository_before_clone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _policy("abcdef0123456789")
    monkeypatch.setattr(history_privacy, "_visibility", lambda *_args: "PUBLIC")

    with pytest.raises(HistoryPrivacyError, match="visibility=PUBLIC"):
        rewrite_history(
            policy,
            repository_url="https://example.invalid/owner/repo.git",
            output_dir=tmp_path / "rewrite",
            execute=True,
            force_push=False,
            confirmation=None,
        )

    assert (tmp_path / "rewrite").is_dir()
    assert not any((tmp_path / "rewrite").iterdir())
