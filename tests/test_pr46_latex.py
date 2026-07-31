from __future__ import annotations

from pathlib import Path

import pytest

from tutor_assistant import latex


def test_remote_latex_blocks_git_push(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="read-only"):
        latex._read_only_remote_git(tmp_path, "push", "origin", "HEAD:main")


def test_remote_latex_allows_read_commands(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[Path, tuple[str, ...]]] = []

    def fake(repo: Path, *args: str, **_kwargs) -> str:
        calls.append((repo, args))
        return "ok"

    monkeypatch.setattr(latex, "_original_remote_run_git", fake)

    assert latex._read_only_remote_git(tmp_path, "fetch", "origin", "main") == "ok"
    assert calls == [(tmp_path, ("fetch", "origin", "main"))]
