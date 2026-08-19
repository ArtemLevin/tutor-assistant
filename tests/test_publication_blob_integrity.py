from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from tutor_assistant.publisher import GitError, _assert_git_blob_matches


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_git_blob_integrity_rejects_staged_content_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    target = repo / "transcript.txt"
    target.write_text("different\n", encoding="utf-8")
    _git(repo, "add", "transcript.txt")
    approved_sha = hashlib.sha256(b"approved\n").hexdigest()

    with pytest.raises(GitError, match="staged Git blob"):
        _assert_git_blob_matches(repo, ":transcript.txt", approved_sha, "staged")


def test_git_blob_integrity_accepts_exact_staged_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    target = repo / "transcript.txt"
    target.write_text("approved\n", encoding="utf-8")
    _git(repo, "add", "transcript.txt")
    approved_sha = hashlib.sha256(b"approved\n").hexdigest()

    _assert_git_blob_matches(repo, ":transcript.txt", approved_sha, "staged")
