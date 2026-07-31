from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from tutor_assistant.publication import (
    GitHubRepositoryIdentity,
    GitRemoteDescriptor,
)
from tutor_assistant.publisher import LessonPublisher, run_git


@pytest.fixture(scope="session", autouse=True)
def git_unicode_paths():
    """Make Git plumbing output stable for Cyrillic repository paths in tests."""

    keys = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.quotepath",
        "GIT_CONFIG_VALUE_0": "false",
    }
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def local_publication_remotes(monkeypatch):
    """Give real-Git tests a synthetic identity while production stays GitHub-only."""

    original = LessonPublisher._descriptor

    def descriptor(self: LessonPublisher, repo: Path):
        raw_url = run_git(repo, "remote", "get-url", "--push", self.config.remote)
        candidate = Path(raw_url)
        windows_absolute = len(raw_url) >= 3 and raw_url[1:3] in {":\\", ":/"}
        if candidate.is_absolute() or windows_absolute:
            owner, repository = self.config.repository_full_name.split("/", 1)
            return GitRemoteDescriptor(
                remote_name=self.config.remote,
                identity=GitHubRepositoryIdentity(
                    host="github.com",
                    owner=owner,
                    repository=repository,
                ),
                url_sha256=hashlib.sha256(raw_url.encode("utf-8")).hexdigest(),
            )
        return original(self, repo)

    monkeypatch.setattr(LessonPublisher, "_descriptor", descriptor)
