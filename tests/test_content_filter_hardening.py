from __future__ import annotations

import asyncio
import threading
import time
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

import tutor_assistant.config as config_module
import tutor_assistant.normalization.http_client as http_client
import tutor_assistant.publisher as publisher_module
from tutor_assistant.config import AppConfig, NormalizationConfig, RepositoryConfig
from tutor_assistant.content import StudentContentService
from tutor_assistant.domain import Lesson, Student
from tutor_assistant.normalization.errors import NormalizationCancelledError
from tutor_assistant.normalization.http_client import cancellable_request
from tutor_assistant.normalization.protocol import CancellationToken
from tutor_assistant.publisher import create_draft_pr, ensure_private_repository


def make_lesson(identifier: str) -> Lesson:
    return Lesson(
        lesson_id=identifier,
        student=Student(id="student", full_name="Тестовый ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 27),
        topic="Логарифмические неравенства",
    )


def test_legacy_conservative_mode_maps_to_filter_only() -> None:
    assert NormalizationConfig(mode="conservative").mode == "filter_only"
    assert NormalizationConfig().mode == "filter_only"


def test_manual_review_cannot_be_disabled() -> None:
    with pytest.raises(ValidationError):
        NormalizationConfig(require_manual_approval=False)


def test_app_config_uses_shared_atomic_writer(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def write(path: Path, content: str) -> None:
        captured.update(path=path, content=content)

    monkeypatch.setattr(config_module, "atomic_write_text", write)
    target = tmp_path / "app.yaml"
    AppConfig().save(target)

    assert captured["path"] == target
    assert "normalization:" in str(captured["content"])
    assert "mode: filter_only" in str(captured["content"])


class _FakeGateway:
    def __init__(self) -> None:
        self.private_checked = False
        self.created = False

    def ensure_private_repository(self) -> None:
        self.private_checked = True

    def find_open_pull_request(self, branch: str, base_branch: str) -> str | None:
        assert branch == "lesson/student"
        assert base_branch == "main"
        return None

    def create_draft_pull_request(self, **kwargs) -> str:
        assert kwargs["branch"] == "lesson/student"
        assert kwargs["base_branch"] == "main"
        assert kwargs["title"].startswith("Lesson:")
        self.created = True
        return "https://github.com/owner/private-students/pull/1"


def test_publisher_uses_rest_gateway_without_gh(monkeypatch, tmp_path: Path) -> None:
    gateway = _FakeGateway()
    monkeypatch.setattr(publisher_module.shutil, "which", lambda _command: None)
    config = RepositoryConfig(repository_full_name="owner/private-students")

    ensure_private_repository(config, tmp_path, gateway)
    url, warnings = create_draft_pr(
        config,
        tmp_path,
        make_lesson("rest-pr"),
        "lesson/student",
        gateway,
    )

    assert gateway.private_checked
    assert gateway.created
    assert url == "https://github.com/owner/private-students/pull/1"
    assert warnings == []


def test_content_service_iterates_all_pages(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    for index in range(5):
        service.create_lesson(make_lesson(f"lesson-{index}"))

    assert len(list(service.iter_lessons(page_size=2))) == 5


def test_cancellable_http_request_interrupts_in_flight_operation(monkeypatch) -> None:
    class HangingAsyncClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def request(self, *_args, **_kwargs):
            await asyncio.Event().wait()

    monkeypatch.setattr(http_client.httpx, "AsyncClient", HangingAsyncClient)
    token = CancellationToken()
    timer = threading.Timer(0.05, token.cancel)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(NormalizationCancelledError):
            cancellable_request(
                "POST",
                "https://example.invalid/filter",
                payload={"input": "test"},
                timeout_seconds=30,
                trust_env=False,
                cancellation=token,
            )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 1
