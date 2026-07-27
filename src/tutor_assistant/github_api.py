from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from .config import RepositoryConfig


class GitHubApiError(RuntimeError):
    pass


class GitHubRepositoryGateway(Protocol):
    def ensure_private_repository(self) -> None: ...

    def find_open_pull_request(self, branch: str, base_branch: str) -> str | None: ...

    def create_draft_pull_request(
        self,
        *,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> str: ...


class GitHubRestGateway:
    """Minimal GitHub REST adapter used when GitHub CLI is unavailable."""

    def __init__(
        self,
        config: RepositoryConfig,
        *,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.config = config
        self.client_factory = client_factory

    def _token(self) -> str:
        names = (self.config.github_token_env, "GH_TOKEN", "GITHUB_TOKEN")
        for name in dict.fromkeys(names):
            token = os.getenv(name, "").strip()
            if token:
                return token
        raise GitHubApiError(
            "GitHub CLI не установлен. Для GitHub REST API задайте переменную "
            f"{self.config.github_token_env} с токеном, имеющим доступ к репозиторию."
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token()}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "tutor-assistant",
        }
        try:
            with self.client_factory(
                base_url="https://api.github.com",
                headers=headers,
                timeout=self.config.github_api_timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = client.request(method, path, params=params, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise GitHubApiError("GitHub API не ответил за отведённое время") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                raise GitHubApiError("GitHub API отклонил токен или его права") from exc
            if status == 404:
                raise GitHubApiError(
                    "GitHub API не нашёл репозиторий или токен не имеет к нему доступа"
                ) from exc
            raise GitHubApiError(f"GitHub API вернул HTTP {status}") from exc
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise GitHubApiError("GitHub API недоступен или вернул некорректный ответ") from exc

    def ensure_private_repository(self) -> None:
        payload = self._request("GET", f"/repos/{self.config.repository_full_name}")
        if not isinstance(payload, dict):
            raise GitHubApiError("GitHub API вернул некорректное описание репозитория")
        visibility = str(payload.get("visibility") or "").upper()
        is_private = bool(payload.get("private")) or visibility == "PRIVATE"
        if not is_private:
            raise GitHubApiError(
                f"Публикация заблокирована: {self.config.repository_full_name} имеет visibility "
                f"{visibility or 'UNKNOWN'}, требуется PRIVATE"
            )

    def find_open_pull_request(self, branch: str, base_branch: str) -> str | None:
        owner = self.config.repository_full_name.split("/", 1)[0]
        payload = self._request(
            "GET",
            f"/repos/{self.config.repository_full_name}/pulls",
            params={"state": "open", "head": f"{owner}:{branch}", "base": base_branch},
        )
        if not isinstance(payload, list):
            raise GitHubApiError("GitHub API вернул некорректный список pull request")
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("html_url"), str):
                return item["html_url"]
        return None

    def create_draft_pull_request(
        self,
        *,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> str:
        payload = self._request(
            "POST",
            f"/repos/{self.config.repository_full_name}/pulls",
            payload={
                "title": title,
                "body": body,
                "head": branch,
                "base": base_branch,
                "draft": True,
            },
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("html_url"), str):
            raise GitHubApiError("GitHub API не вернул URL созданного pull request")
        return payload["html_url"]
