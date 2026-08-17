from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import date
from pathlib import Path

from tutor_assistant.application.recording_stop import (
    RecordingStopSession,
    RecordingStopState,
    StopRecordingUseCase,
)
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.recording import RecordingResult
from tutor_assistant.ui.recording_finalize_app import MainWindow as StopFinalizeMainWindow


class FakeLease:
    def __init__(self, events: list[str], *, fail_release: bool = False) -> None:
        self.events = events
        self.fail_release = fail_release
        self.release_calls = 0

    def release(self) -> None:
        self.events.append("lease.release")
        self.release_calls += 1
        if self.fail_release:
            raise RuntimeError("release boom")


class FakeRecorder:
    def __init__(
        self,
        result: RecordingResult,
        events: list[str],
        *,
        fail_stop: bool = False,
    ) -> None:
        self.result = result
        self.events = events
        self.fail_stop = fail_stop
        self._active = True
        self.stop_calls = 0

    @property
    def active(self) -> bool:
        return self._active

    def stop(self) -> RecordingResult:
        self.events.append("recorder.stop")
        self.stop_calls += 1
        self._active = False
        if self.fail_stop:
            raise RuntimeError("writer boom")
        return self.result


class FakePipeline:
    def __init__(self, events: list[str], *, fail_first_save: bool = False) -> None:
        self.events = events
        self.fail_first_save = fail_first_save
        self.save_calls = 0
        self.saved: list[tuple[JobStatus, tuple[str, ...], str | None]] = []

    def save_state(self, lesson: Lesson, *fields: str, **_kwargs: object) -> Lesson:
        self.save_calls += 1
        self.events.append(f"lesson.save:{lesson.status.value}")
        if self.fail_first_save and self.save_calls == 1:
            raise RuntimeError("database boom")
        self.saved.append((lesson.status, fields, lesson.error))
        return lesson


def make_lesson() -> Lesson:
    lesson = Lesson(
        student=Student(id="student", full_name="Student"),
        subject="Математика",
        topic="Векторы",
        lesson_date=date(2026, 8, 17),
    )
    lesson.transition(JobStatus.RECORDING)
    return lesson


def make_result(tmp_path: Path, *, name: str = "lesson.wav") -> RecordingResult:
    mixed = tmp_path / name
    mixed.write_bytes(b"audio")
    quality = tmp_path / "quality.json"
    quality.write_text('{"ready": true, "warnings": []}', encoding="utf-8")
    return RecordingResult(
        microphone_file=tmp_path / "microphone.wav",
        system_file=tmp_path / "system.wav",
        mixed_file=mixed,
        session_file=tmp_path / "session.json",
        sync_report=tmp_path / "sync.json",
        quality_report=quality,
    )


def test_stop_success_finalizes_persists_and_releases_once(tmp_path: Path) -> None:
    events: list[str] = []
    lesson = make_lesson()
    raw_result = make_result(tmp_path)
    readable = tmp_path / "Student_2026-08-17.wav"
    readable.write_bytes(b"audio")
    recorder = FakeRecorder(raw_result, events)
    lease = FakeLease(events)
    pipeline = FakePipeline(events)

    def finalize(result: RecordingResult, expected_lesson: Lesson) -> RecordingResult:
        assert expected_lesson is lesson
        events.append("result.finalize")
        return replace(result, mixed_file=readable)

    outcome = StopRecordingUseCase(pipeline, result_finalizer=finalize).stop(
        RecordingStopSession(lesson=lesson, recorder=recorder, lease=lease)
    )

    assert outcome.state == RecordingStopState.RECORDED
    assert outcome.result is not None
    assert outcome.result.mixed_file == readable
    assert lesson.status == JobStatus.RECORDED
    assert lesson.source_audio_local == str(readable.resolve())
    assert lease.release_calls == 1
    assert events == [
        "recorder.stop",
        "result.finalize",
        "lesson.save:recorded",
        "lease.release",
    ]
    assert pipeline.saved == [
        (
            JobStatus.RECORDED,
            ("source_audio_local", "status", "error"),
            None,
        )
    ]


def test_recorder_stop_failure_requires_recovery_and_keeps_recording_status(tmp_path: Path) -> None:
    events: list[str] = []
    lesson = make_lesson()
    recorder = FakeRecorder(make_result(tmp_path), events, fail_stop=True)
    lease = FakeLease(events)
    pipeline = FakePipeline(events)

    outcome = StopRecordingUseCase(pipeline).stop(
        RecordingStopSession(lesson=lesson, recorder=recorder, lease=lease)
    )

    assert outcome.state == RecordingStopState.RECOVERY_REQUIRED
    assert outcome.result is None
    assert "writer boom" in (outcome.error or "")
    assert lesson.status == JobStatus.RECORDING
    assert lesson.source_audio_local is None
    assert pipeline.saved == []
    assert lease.release_calls == 1
    assert events == ["recorder.stop", "lease.release"]


def test_finalizer_failure_marks_lesson_failed_after_audio_exists(tmp_path: Path) -> None:
    events: list[str] = []
    lesson = make_lesson()
    raw_result = make_result(tmp_path)
    recorder = FakeRecorder(raw_result, events)
    lease = FakeLease(events)
    pipeline = FakePipeline(events)

    def fail_finalizer(_result: RecordingResult, _lesson: Lesson) -> RecordingResult:
        events.append("result.finalize")
        raise RuntimeError("rename boom")

    outcome = StopRecordingUseCase(pipeline, result_finalizer=fail_finalizer).stop(
        RecordingStopSession(lesson=lesson, recorder=recorder, lease=lease)
    )

    assert outcome.state == RecordingStopState.FAILED
    assert outcome.result is raw_result
    assert "rename boom" in (outcome.error or "")
    assert lesson.status == JobStatus.FAILED
    assert "rename boom" in (lesson.error or "")
    assert pipeline.saved[-1][0] == JobStatus.FAILED
    assert lease.release_calls == 1
    assert events == [
        "recorder.stop",
        "result.finalize",
        "lesson.save:failed",
        "lease.release",
    ]


def test_persistence_failure_after_stop_is_compensated_to_failed(tmp_path: Path) -> None:
    events: list[str] = []
    lesson = make_lesson()
    result = make_result(tmp_path)
    recorder = FakeRecorder(result, events)
    lease = FakeLease(events)
    pipeline = FakePipeline(events, fail_first_save=True)

    outcome = StopRecordingUseCase(pipeline).stop(
        RecordingStopSession(lesson=lesson, recorder=recorder, lease=lease)
    )

    assert outcome.state == RecordingStopState.FAILED
    assert outcome.result is result
    assert "database boom" in (outcome.error or "")
    assert lesson.status == JobStatus.FAILED
    assert lesson.source_audio_local == str(result.mixed_file.resolve())
    assert pipeline.saved[-1][0] == JobStatus.FAILED
    assert lease.release_calls == 1
    assert events == [
        "recorder.stop",
        "lesson.save:recorded",
        "lesson.save:failed",
        "lease.release",
    ]


def test_missing_lease_does_not_block_safe_stop(tmp_path: Path) -> None:
    events: list[str] = []
    lesson = make_lesson()
    result = make_result(tmp_path)
    recorder = FakeRecorder(result, events)
    pipeline = FakePipeline(events)

    outcome = StopRecordingUseCase(pipeline).stop(
        RecordingStopSession(lesson=lesson, recorder=recorder, lease=None)
    )

    assert outcome.state == RecordingStopState.RECORDED
    assert lesson.status == JobStatus.RECORDED
    assert events == ["recorder.stop", "lesson.save:recorded"]


def test_lease_release_failure_does_not_change_recorded_outcome(tmp_path: Path) -> None:
    events: list[str] = []
    lesson = make_lesson()
    result = make_result(tmp_path)
    recorder = FakeRecorder(result, events)
    lease = FakeLease(events, fail_release=True)
    pipeline = FakePipeline(events)

    outcome = StopRecordingUseCase(pipeline).stop(
        RecordingStopSession(lesson=lesson, recorder=recorder, lease=lease)
    )

    assert outcome.state == RecordingStopState.RECORDED
    assert lesson.status == JobStatus.RECORDED
    assert lease.release_calls == 1


def test_production_stop_does_not_delegate_back_to_legacy_super() -> None:
    source = inspect.getsource(StopFinalizeMainWindow._stop_recording_async)

    assert "stop_recording_use_case.stop" in source
    assert "super()._stop_recording_async" not in source


def test_stop_use_case_module_is_qt_independent() -> None:
    import tutor_assistant.application.recording_stop as module

    source = inspect.getsource(module)
    assert "PySide" not in source


def test_stop_finalize_adapter_remains_in_production_mro() -> None:
    from tutor_assistant.ui.recording_recovery_app import MainWindow as ProductionMainWindow

    assert issubclass(ProductionMainWindow, StopFinalizeMainWindow)
