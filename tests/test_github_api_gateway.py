from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from tutor_assistant.config import RepositoryConfig
from tutor_assistant.github_api import GitHubApiError, GitHubRestGateway


def json_response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def gateway(
    handler: Callable[[httpx.Request], httpx.Response],
    monkeypatch,
    *,
    token: str | None = "test-only-token",
) -> GitHubRestGateway:
    for variable in ("PRIVATE_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(variable, raising=False)
    if token is not None:
        monkeypatch.setenv("PRIVATE_GITHUB_TOKEN", token)
    configuration = RepositoryConfig(
        repository_full_name="teacher/private-lessons",
        github_token_env="PRIVATE_GITHUB_TOKEN",
        github_api_timeout_seconds=17,
    )
    return GitHubRestGateway(
        configuration,
        client_factory=lambda **options: httpx.Client(
            **options,
            transport=httpx.MockTransport(handler),
        ),
    )


@pytest.mark.parametrize("payload", [{"private": True}, {"visibility": "private"}])
def test_private_repository_validation_accepts_github_private_contract(
    payload,
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    gateway(handler, monkeypatch).ensure_private_repository()

    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.github.com/repos/teacher/private-lessons"
    assert requests[0].headers["authorization"] == "Bearer test-only-token"
    assert requests[0].headers["x-github-api-version"] == "2022-11-28"


@pytest.mark.parametrize(
    "payload",
    [
        {"private": False, "visibility": "public"},
        {"private": False},
        {"private": "false", "visibility": "public"},
        {"private": 1, "visibility": "public"},
    ],
)
def test_public_or_malformed_repository_response_never_allows_lesson_publication(
    payload,
    monkeypatch,
) -> None:
    client = gateway(lambda _request: httpx.Response(200, json=payload), monkeypatch)

    with pytest.raises(GitHubApiError, match="требуется PRIVATE"):
        client.ensure_private_repository()


@pytest.mark.parametrize("payload", [[], "private", None])
def test_repository_metadata_must_be_a_json_object(payload, monkeypatch) -> None:
    client = gateway(lambda _request: json_response(200, payload), monkeypatch)

    with pytest.raises(GitHubApiError, match="некорректное описание"):
        client.ensure_private_repository()


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "отклонил токен"),
        (403, "отклонил токен"),
        (404, "не нашёл репозиторий"),
        (429, "HTTP 429"),
        (500, "HTTP 500"),
    ],
)
def test_http_status_errors_have_actionable_context_without_exposing_token(
    status,
    message,
    monkeypatch,
) -> None:
    client = gateway(lambda _request: httpx.Response(status, json={"message": "failure"}), monkeypatch)

    with pytest.raises(GitHubApiError, match=message) as captured:
        client.ensure_private_repository()

    assert "test-only-token" not in str(captured.value)


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("timeout", "не ответил"),
        ("connection", "недоступен"),
        ("malformed-json", "недоступен"),
    ],
)
def test_network_timeout_connection_and_invalid_json_are_normalized(
    failure,
    message,
    monkeypatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("network timeout", request=request)
        if failure == "connection":
            raise httpx.ConnectError("network unavailable", request=request)
        return httpx.Response(200, content=b"{invalid-json")

    with pytest.raises(GitHubApiError, match=message):
        gateway(handler, monkeypatch).ensure_private_repository()


def test_missing_github_token_rejects_publication_without_network_request(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"private": True})

    with pytest.raises(GitHubApiError, match="PRIVATE_GITHUB_TOKEN"):
        gateway(handler, monkeypatch, token=None).ensure_private_repository()

    assert requests == []


@pytest.mark.parametrize("fallback", ["GH_TOKEN", "GITHUB_TOKEN"])
def test_standard_token_environment_variables_are_supported_as_fallback(
    fallback,
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []
    client = gateway(
        lambda request: requests.append(request) or httpx.Response(200, json={"private": True}),
        monkeypatch,
        token=None,
    )
    monkeypatch.setenv(fallback, "  fallback-token  ")

    client.ensure_private_repository()

    assert requests[0].headers["authorization"] == "Bearer fallback-token"


def test_open_pull_request_search_uses_repository_owner_branch_and_base(monkeypatch) -> None:
    requests: list[httpx.Request] = []
    expected = "https://github.com/teacher/private-lessons/pull/17"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[None, {"number": 1}, {"html_url": expected}])

    result = gateway(handler, monkeypatch).find_open_pull_request("lesson-17", "main")

    assert result == expected
    assert requests[0].url.params == httpx.QueryParams(
        {"state": "open", "head": "teacher:lesson-17", "base": "main"}
    )


@pytest.mark.parametrize("payload", [{"html_url": "not-a-list"}, None, "invalid"])
def test_open_pull_request_search_rejects_non_list_response(payload, monkeypatch) -> None:
    client = gateway(lambda _request: json_response(200, payload), monkeypatch)

    with pytest.raises(GitHubApiError, match="некорректный список"):
        client.find_open_pull_request("lesson", "main")


def test_open_pull_request_search_ignores_entries_without_string_url(monkeypatch) -> None:
    client = gateway(
        lambda _request: httpx.Response(200, json=[None, {}, {"html_url": 123}]),
        monkeypatch,
    )

    assert client.find_open_pull_request("lesson", "main") is None


def test_new_pull_requests_are_always_created_as_drafts(monkeypatch) -> None:
    requests: list[httpx.Request] = []
    expected = "https://github.com/teacher/private-lessons/pull/18"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"html_url": expected})

    result = gateway(handler, monkeypatch).create_draft_pull_request(
        branch="lesson-18",
        base_branch="main",
        title="Проверенный транскрипт",
        body="Только подтверждённый текст",
    )

    assert result == expected
    assert requests[0].method == "POST"
    assert json.loads(requests[0].content) == {
        "title": "Проверенный транскрипт",
        "body": "Только подтверждённый текст",
        "head": "lesson-18",
        "base": "main",
        "draft": True,
    }


@pytest.mark.parametrize("payload", [{}, {"html_url": 123}, [], None])
def test_pull_request_creation_rejects_missing_or_invalid_html_url(payload, monkeypatch) -> None:
    client = gateway(lambda _request: json_response(201, payload), monkeypatch)

    with pytest.raises(GitHubApiError, match="не вернул URL"):
        client.create_draft_pull_request(
            branch="lesson",
            base_branch="main",
            title="title",
            body="body",
        )
