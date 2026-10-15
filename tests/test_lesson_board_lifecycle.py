from __future__ import annotations

import base64
import json
from datetime import date
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from tutor_assistant.application.lesson_board import BoardLaunchError, LessonBoardCoordinator
from tutor_assistant.config import AppConfig, TutorBoardConfig
from tutor_assistant.domain import Lesson, LessonBoardState, Student
from tutor_assistant.pipeline import LessonPipeline


def make_lesson() -> Lesson:
    return Lesson(
        student=Student(id="pupil", full_name="Ученик"),
        subject="math",
        topic="Линейные функции",
        lesson_date=date(2026, 10, 15),
        board=LessonBoardState(enabled=True, status="pending"),
    )


def make_pipeline(tmp_path: Path) -> LessonPipeline:
    return LessonPipeline(AppConfig(workspace=tmp_path))


def test_legacy_lesson_and_config_remain_compatible() -> None:
    lesson = make_lesson()
    dumped = lesson.model_dump()
    del dumped["board"]
    old = Lesson.model_validate(dumped)
    assert old.board == LessonBoardState()
    assert not AppConfig().board.enabled
    assert TutorBoardConfig(base_url="https://board.example").base_url == "https://board.example"
    with pytest.raises(ValidationError):
        TutorBoardConfig(enabled=True, base_url="http://untrusted.example")
    with pytest.raises(ValidationError):
        LessonBoardState(enabled=True, status="linked")
    with pytest.raises(ValidationError):
        LessonBoardState(enabled=True, status="linked", board_id="../escape")


def test_launch_uses_live_teacher_session_and_persists_association(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path)
    lesson = make_lesson()
    pipeline.create(lesson)
    coordinator = LessonBoardCoordinator(pipeline, "https://board.example")
    launch, url = coordinator.launch(lesson)
    try:
        assert url.startswith("https://board.example/boards#ta-start=")
        encoded = url.partition("#ta-start=")[2]
        launch_data = json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        )
        assert launch_data["lessonId"] == lesson.lesson_id
        assert launch_data["version"] == 1
        assert "Уч" not in url
        assert "nonce" not in url.partition("#")[0]
        parsed = urlsplit(launch_data["callback"])
        request = f"/prepare?{parsed.query}"
        completion = f"/complete?{parsed.query}&boardId=board%3Alesson-123"

        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=4)
        connection.request("GET", request, headers={"Origin": "https://attacker.example"})
        denied = connection.getresponse()
        assert denied.status == 403
        denied.read()
        connection.close()

        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=4)
        connection.request("GET", request, headers={"Origin": "https://board.example"})
        approved = connection.getresponse()
        assert approved.status == 200
        assert approved.getheader("Access-Control-Allow-Origin") == "https://board.example"
        assert json.loads(approved.read()) == {"valid": True}
        connection.close()

        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=4)
        connection.request("GET", completion.replace("board%3Alesson-123", "..%2Fwrong"))
        bad_id = connection.getresponse()
        assert bad_id.status == 403
        bad_id.read()
        connection.close()

        coordinator.recording_finished(lesson.lesson_id)
        assert pipeline.store.get(lesson.lesson_id).board.recording_finished

        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=4)
        connection.request("GET", completion)
        linked = connection.getresponse()
        assert linked.status == 303
        assert linked.getheader("Location") == "https://board.example/b/board%3Alesson-123#/board"
        linked.read()
        connection.close()

        stored = pipeline.store.get(lesson.lesson_id)
        assert stored is not None
        assert stored.board.status == "linked"
        assert stored.board.board_id == "board:lesson-123"
        assert stored.board.pdf_status == "pending"
        assert stored.board.recording_finished is True
        # Independently replaying the same identity is idempotent, while hijacking is blocked.
        coordinator.link(lesson.lesson_id, "board:lesson-123")
        with pytest.raises(BoardLaunchError):
            coordinator.link(lesson.lesson_id, "board:other")
        assert pipeline.store.get(lesson.lesson_id).board.board_id == "board:lesson-123"
    finally:
        launch.close()


def test_board_launch_failure_is_isolated_from_audio_state(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path)
    lesson = make_lesson()
    pipeline.create(lesson)
    coordinator = LessonBoardCoordinator(pipeline, "")
    with pytest.raises(BoardLaunchError):
        coordinator.launch(lesson)
    coordinator.launch_failed(lesson.lesson_id, "Недоступен URL доски")
    recorded = pipeline.store.get(lesson.lesson_id)
    assert recorded is not None
    assert recorded.board.status == "failed"
    assert recorded.board.error == "Недоступен URL доски"
    assert recorded.status == lesson.status
    coordinator.recording_finished(lesson.lesson_id)
    assert pipeline.store.get(lesson.lesson_id).board.recording_finished


def test_linked_board_requires_correct_server_id_and_single_association(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path)
    lesson = make_lesson()
    pipeline.create(lesson)
    coordinator = LessonBoardCoordinator(pipeline, "https://board.example")
    with pytest.raises(BoardLaunchError):
        coordinator.link(lesson.lesson_id, "board/id")
    coordinator.link(lesson.lesson_id, "board:expected")
    coordinator.recording_finished(lesson.lesson_id)
    assert pipeline.store.get(lesson.lesson_id).board.pdf_status == "pending"
    coordinator.recording_finished(lesson.lesson_id)
    assert pipeline.store.get(lesson.lesson_id).board.board_id == "board:expected"
