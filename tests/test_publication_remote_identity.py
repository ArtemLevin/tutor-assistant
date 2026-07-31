from __future__ import annotations

import pytest

from tutor_assistant.publication import (
    RemoteIdentityError,
    assert_expected_repository,
    parse_github_remote_url,
)


def test_https_remote_identity() -> None:
    identity = parse_github_remote_url(
        "https://github.com/ArtemLevin/students-26-27.git"
    )
    assert identity.full_name == "ArtemLevin/students-26-27"


def test_ssh_remote_identity() -> None:
    identity = parse_github_remote_url(
        "git@github.com:ArtemLevin/students-26-27.git"
    )
    assert identity.full_name == "ArtemLevin/students-26-27"


def test_other_host_is_rejected() -> None:
    with pytest.raises(RemoteIdentityError):
        parse_github_remote_url("https://example.org/owner/repository.git")


def test_configured_repository_must_match_remote() -> None:
    identity = parse_github_remote_url(
        "https://github.com/ArtemLevin/students-26-27.git"
    )
    assert_expected_repository(identity, "artemlevin/STUDENTS-26-27")
    with pytest.raises(RemoteIdentityError):
        assert_expected_repository(identity, "ArtemLevin/other-repository")
