from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from tutor_assistant.domain import Lesson, LessonProcessingMode, Student
from tutor_assistant.recording import RecordingResult
from tutor_assistant.ui.recording_finalize_app import MainWindow as RecordingFinalizeMainWindow
from tutor_assistant.ui.recording_presentation import RecordingPanelPhase


class _Control:
    def __init__(self) -> None:
        self.enabled: bool | None = None
        self.text: str | None = None
        self.value: int | None = None
        self.range: tuple[int, int] | None = None
        self.cleared = 0

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def setText(self, text: str) -> None:
        self.text = text

    def setValue(self, value: int) -> None:
        self.value = value

    def setRange(self, minimum: int, maximum: int) -> None:
        self.range = (minimum, maximum)

    def clear(self) -> None:
        self.cleared += 1


class _Config:
    def __init__(self) -> None:
        self.quick_start = SimpleNamespace(last_topic="old topic")
        self.saved: list[Path] = []

    def save(self, path: Path) -> None:
        self.saved.append(path)


class _Workflow:
    def __init__(self) -> None:
        self.completed = 0

    def mark_completed(self) -> None:
        self.completed += 1


class _FinalizeHarness:
    _prepare_next_lesson = RecordingFinalizeMainWindow._prepare_next_lesson
    _present_recording_completed = RecordingFinalizeMainWindow._present_recording_completed

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = _Config()
        self.review_lesson = None
        self.lesson = None
        self.recording_lesson = object()
        self.recorder = object()
        self._recording_stop_started = True
        self._quick_auto_transcribe_active = False
        self.recording_workflow = _Workflow()

        self.start_button = _Control()
        self.quick_start_button = _Control()
        self.stop_button = _Control()
        self.test_devices_button = _Control()
        self.audio_path = _Control()
        self.topic = _Control()
        self.quick_topic = _Control()
        self.duration = _Control()
        self.progress = _Control()
        self.transcribe_button = _Control()

        self.statuses: list[tuple[str, str | None]] = []
        self.phases: list[RecordingPanelPhase] = []
        self.enqueued: list[tuple[Lesson, Path]] = []
        self.readiness_refreshes = 0
        self.quick_resets: list[bool] = []
        self.schedule_updates: list[tuple[str, str | None, bool]] = []

    def _update_scheduled_occurrence(
        self,
        status: str,
        *,
        lesson_id: str | None = None,
        clear: bool = False,
    ) -> None:
        self.schedule_updates.append((status, lesson_id, clear))

    def _reset_quick_processing_selection(self, *, clear_selection: bool) -> None:
        self.quick_resets.append(clear_selection)

    def _set_recording_panel_phase(self, phase: RecordingPanelPhase) -> None:
        self.phases.append(phase)

    def _set_status(self, message: str, tone: str | None = None) -> None:
        self.statuses.append((message, tone))

    def _enqueue_transcription(self, lesson: Lesson, audio: Path) -> None:
        self.enqueued.append((lesson, audio))

    def _refresh_quick_readiness(self) -> None:
        self.readiness_refreshes += 1


def _lesson() -> Lesson:
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 5),
        topic="Волны",
    )
    lesson.pipeline.processing_mode = LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB
    return lesson


def _recording_result(tmp_path: Path) -> RecordingResult:
    quality_report = tmp_path / "audio_quality_report.json"
    quality_report.write_text(
        json.dumps({"ready": True, "warnings": []}),
        encoding="utf-8",
    )
    return RecordingResult(
        microphone_file=tmp_path / "microphone.wav",
        system_file=tmp_path / "system.wav",
        mixed_file=tmp_path / "lesson.m4a",
        session_file=tmp_path / "session.json",
        sync_report=tmp_path / "sync_report.json",
        quality_report=quality_report,
    )


def test_completed_auto_recording_prepares_next_lesson_without_crash(tmp_path) -> None:
    harness = _FinalizeHarness(tmp_path / "config.toml")
    lesson = _lesson()
    result = _recording_result(tmp_path)
    outcome = SimpleNamespace(lesson=lesson, result=result)

    harness._present_recording_completed(outcome, None)

    assert harness.recording_workflow.completed == 1
    assert harness.recording_lesson is None
    assert harness.recorder is None
    assert harness._recording_stop_started is False
    assert harness.enqueued == [(lesson, result.mixed_file)]
    assert harness.quick_resets == [True]
    assert harness.topic.cleared == 1
    assert harness.quick_topic.cleared == 1
    assert harness.config.quick_start.last_topic == ""
    assert harness.config.saved == [harness.config_path]
    assert harness.duration.text == "00:00:00"
    assert harness.progress.range == (0, 1)
    assert harness.progress.value == 0
    assert harness.transcribe_button.enabled is True
    assert harness.quick_start_button.text == "Начать занятие"
    assert harness.phases[-1] == RecordingPanelPhase.READY
    assert harness.readiness_refreshes == 1


def test_prepare_next_lesson_preserves_parallel_review_audio_context(tmp_path) -> None:
    harness = _FinalizeHarness(tmp_path / "config.toml")
    review_lesson = _lesson()
    harness.lesson = review_lesson
    harness.review_lesson = review_lesson

    harness._prepare_next_lesson()

    assert harness.lesson is review_lesson
    assert harness.review_lesson is review_lesson
    assert harness.audio_path.cleared == 0
    assert harness.recording_lesson is None
    assert harness.phases[-1] == RecordingPanelPhase.READY


def test_completed_recording_releases_workflow_before_optional_post_finalize_failure(
    tmp_path,
) -> None:
    harness = _FinalizeHarness(tmp_path / "config.toml")
    lesson = _lesson()
    result = _recording_result(tmp_path)
    outcome = SimpleNamespace(lesson=lesson, result=result)

    def fail_enqueue(_lesson: Lesson, _audio: Path) -> None:
        raise RuntimeError("queue unavailable")

    harness._enqueue_transcription = fail_enqueue

    with pytest.raises(RuntimeError, match="queue unavailable"):
        harness._present_recording_completed(outcome, None)

    assert harness.recording_workflow.completed == 1
    assert harness.recording_lesson is None
    assert harness.recorder is None
    assert harness._recording_stop_started is False
