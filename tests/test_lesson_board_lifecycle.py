from __future__ import annotations

import base64
import json
from datetime import date
from threading import Event
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from tutor_assistant.application.lesson_board import (
    BoardLaunchError,
    LessonBoardCoordinator,
    LocalBoardLaunch,
)
from tutor_assistant.config import AppConfig, TutorBoardConfig
from tutor_assistant.domain import JobStatus, Lesson, LessonBoardState, Student
from tutor_assistant.pipeline import LessonPipeline


def make_lesson() -> Lesson:
    return Lesson(
        student=Student(id="pupil", full_name="Ученик"),
        subject="math",
        topic="Линейные функции",
        lesson_date=date(2026, 10, 15),
        board=LessonBoardState(
            enabled=True,
            status="pending",
            owner_user_id="teacher-user-1",
            organization_id="organization-1",
        ),
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
    with pytest.raises(ValidationError):
        TutorBoardConfig(enabled=True, base_url="https://board.example")
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
    coordinator = LessonBoardCoordinator(
        pipeline, "https://board.example",
        expected_user_id="teacher-user-1",
        expected_organization_id="organization-1",
    )
    lease = pipeline.content_service.acquire_activity(
        "recording", lesson_id=lesson.lesson_id
    )
    launch, url = coordinator.launch(lesson, recording_lease=lease)
    try:
        assert url.startswith("https://board.example/boards#ta-start=")
        encoded = url.partition("#ta-start=")[2]
        launch_data = json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        )
        assert launch_data["lessonId"] == lesson.lesson_id
        assert launch_data["version"] == 1
        assert launch_data["teacherUserId"] == "teacher-user-1"
        assert launch_data["organizationId"] == "organization-1"
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

        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=4)
        connection.request("GET", completion)
        linked = connection.getresponse()
        assert linked.status == 303
        assert linked.getheader("Location") == "https://board.example/b/board%3Alesson-123#/board"
        linked.read()
        connection.close()

        # The association must persist while the recording lease is held.
        during_recording = pipeline.store.get(lesson.lesson_id)
        assert during_recording.board.status == "linked"
        lease.release()
        coordinator.recording_finished(lesson.lesson_id)

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
        if lease.valid:
            lease.release()


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

def test_stale_board_callback_retries_without_losing_stop_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline = make_pipeline(tmp_path)
    lesson = make_lesson()
    pipeline.create(lesson)
    coordinator = LessonBoardCoordinator(pipeline, "https://board.example")
    save_state = pipeline.save_state
    injected = False

    def competing_completion(current: Lesson, *fields: str, **kwargs: object) -> Lesson:
        nonlocal injected
        if not injected:
            injected = True
            competing = pipeline.content_service.get_lesson(current.lesson_id).lesson
            competing.board.recording_finished = True
            save_state(competing, "board")
        return save_state(current, *fields, **kwargs)

    monkeypatch.setattr(pipeline, "save_state", competing_completion)
    coordinator.link(lesson.lesson_id, "board:concurrent")

    stored = pipeline.store.get(lesson.lesson_id)
    assert stored is not None
    assert stored.board.board_id == "board:concurrent"
    assert stored.board.recording_finished
    assert stored.board.pdf_status == "pending"


def test_late_board_callback_preserves_recording_completion(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path)
    lesson = make_lesson()
    pipeline.create(lesson)
    coordinator = LessonBoardCoordinator(pipeline, "https://board.example")
    coordinator.recording_finished(lesson.lesson_id)
    coordinator.link(lesson.lesson_id, "board:late")
    stored = pipeline.store.get(lesson.lesson_id)
    assert stored is not None
    assert stored.board.status == "linked"
    assert stored.board.recording_finished
    assert stored.board.pdf_status == "pending"


def test_launch_requires_exact_pinned_teacher_and_organization(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path)
    lesson = make_lesson()
    pipeline.create(lesson)
    coordinator = LessonBoardCoordinator(
        pipeline, "https://board.example",
        expected_user_id="other-teacher",
        expected_organization_id="organization-1",
    )
    with pytest.raises(BoardLaunchError, match="identity"):
        coordinator.launch(lesson)


def test_expired_listener_marks_failed_and_explicit_retry_restores_pending(
    tmp_path: Path,
) -> None:
    pipeline = make_pipeline(tmp_path)
    lesson = make_lesson()
    pipeline.create(lesson)
    coordinator = LessonBoardCoordinator(pipeline, "https://board.example")
    expired = Event()
    launch = LocalBoardLaunch(
        lesson=lesson,
        base_url="https://board.example",
        on_linked=lambda board_id: coordinator.link(lesson.lesson_id, board_id),
        on_expired=lambda: (
            coordinator.launch_failed(lesson.lesson_id, "expired"), expired.set()
        ),
        lifetime_seconds=0.01,
    )
    launch.start()
    try:
        assert expired.wait(5)
        assert pipeline.store.get(lesson.lesson_id).board.status == "failed"
        restored = coordinator.retry_launch(lesson.lesson_id)
        assert restored.board.status == "pending"
    finally:
        launch.close()


def test_restart_reconciles_orphan_handoffs_and_recorded_audio(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path)
    lesson = make_lesson()
    pipeline.create(lesson)
    lesson.source_audio_local = str(tmp_path / "audio.wav")
    lesson.transition(JobStatus.RECORDED)
    pipeline.save_state(lesson, "source_audio_local", "status")
    coordinator = LessonBoardCoordinator(pipeline, "https://board.example")
    assert coordinator.reconcile() == (1, 1)
    recovered = pipeline.store.get(lesson.lesson_id)
    assert recovered is not None
    assert recovered.board.status == "failed"
    assert recovered.board.recording_finished
    assert coordinator.reconcile() == (0, 0)
