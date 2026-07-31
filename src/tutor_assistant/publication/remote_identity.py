from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


class RemoteIdentityError(ValueError):
    """Raised when a Git remote cannot be bound to the configured GitHub repository."""


@dataclass(frozen=True, slots=True)
class GitHubRepositoryIdentity:
    host: str
    owner: str
    repository: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repository}"


@dataclass(frozen=True, slots=True)
class GitRemoteDescriptor:
    remote_name: str
    identity: GitHubRepositoryIdentity
    url_sha256: str


_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_SCP_REMOTE = re.compile(
    r"^(?P<user>[^@/:]+)@(?P<host>[^:/]+):(?P<owner>[^/]+)/(?P<repo>[^/]+)$"
)


def _repository_name(value: str) -> str:
    name = value[:-4] if value.casefold().endswith(".git") else value
    if not name or not _PATH_COMPONENT.fullmatch(name):
        raise RemoteIdentityError("Git remote содержит некорректное имя репозитория")
    return name


def _identity(host: str, owner: str, repository: str) -> GitHubRepositoryIdentity:
    normalized_host = host.casefold().strip(".")
    if normalized_host != "github.com":
        raise RemoteIdentityError(
            f"Публикация разрешена только через github.com; получен host={normalized_host or 'unknown'}"
        )
    if not _PATH_COMPONENT.fullmatch(owner):
        raise RemoteIdentityError("Git remote содержит некорректного владельца репозитория")
    return GitHubRepositoryIdentity(
        host=normalized_host,
        owner=owner,
        repository=_repository_name(repository),
    )


def parse_github_remote_url(value: str) -> GitHubRepositoryIdentity:
    raw = value.strip()
    if not raw:
        raise RemoteIdentityError("Git push remote не настроен")

    scp = _SCP_REMOTE.fullmatch(raw)
    if scp:
        if scp.group("user") != "git":
            raise RemoteIdentityError("SSH GitHub remote должен использовать пользователя git")
        return _identity(scp.group("host"), scp.group("owner"), scp.group("repo"))

    parsed = urlsplit(raw)
    if parsed.scheme not in {"https", "ssh"}:
        raise RemoteIdentityError("Поддерживаются только HTTPS и SSH GitHub remote")
    if parsed.query or parsed.fragment:
        raise RemoteIdentityError("Git remote не должен содержать query или fragment")
    if parsed.scheme == "https" and (parsed.username or parsed.password):
        raise RemoteIdentityError("Credentials в Git remote URL запрещены")
    if parsed.scheme == "ssh":
        if parsed.password:
            raise RemoteIdentityError("Пароль в SSH Git remote URL запрещён")
        if parsed.username not in {None, "git"}:
            raise RemoteIdentityError("SSH GitHub remote должен использовать пользователя git")

    parts = [item for item in parsed.path.split("/") if item]
    if len(parts) != 2:
        raise RemoteIdentityError("Git remote должен иметь вид owner/repository")
    return _identity(parsed.hostname or "", parts[0], parts[1])


def describe_push_remote(remote_name: str, raw_url: str) -> GitRemoteDescriptor:
    return GitRemoteDescriptor(
        remote_name=remote_name,
        identity=parse_github_remote_url(raw_url),
        url_sha256=hashlib.sha256(raw_url.strip().encode("utf-8")).hexdigest(),
    )


def assert_expected_repository(
    actual: GitHubRepositoryIdentity,
    configured_full_name: str,
) -> None:
    configured = configured_full_name.strip().strip("/")
    if not configured or configured.count("/") != 1:
        raise RemoteIdentityError("Укажите repository.repository_full_name в формате owner/repository")
    if actual.full_name.casefold() != configured.casefold():
        raise RemoteIdentityError(
            "Локальный Git push remote указывает на другой репозиторий: "
            f"получен {actual.full_name}, ожидался {configured}"
        )
