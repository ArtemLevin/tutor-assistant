from __future__ import annotations

import os

import pytest


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
