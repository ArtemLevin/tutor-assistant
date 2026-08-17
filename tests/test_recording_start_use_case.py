from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from tutor_assistant.application.recording import StartRecordingUseCase
from tutor_assistant.domain import JobStatus, Lesson, Student


class FakeLease:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.released = False

    def release(self) -> None:
        self.events.append("lease.release")
        self.released = True


class FakeActivities:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.lease = FakeLease(events)
        self.calls: list[tuple[str, str | None, timedelta]] = []

    def acquire_activity(
        self,
        activity: str,
        *,
        lesson_id: str | None = None,
        exclusive: bool = False,
        ttl: timedelta = timedelta(minutes=2),
    ) -> FakeLease:
        del exclusive
        self.events.append("lease.acquire")
        self.calls.append((activity, lesson_id, ttl))
        if self.fail:
            raise RuntimeError("lease busy")
        return self.lease


class FakePipeline:
    def __init__(self, root: Path, events: list[str], *, fail_create: bool = False) -> None:
        self.root = root
        self.events = events
        self.fail_create = fail_create
        self.saved: list[tuple[JobStatus, tuple[str, ...], str | None]] = []

    def create(self, lesson: Lesson) -> Path:
        self.events.append("lesson.create")
        if self.fail_create:
            raise RuntimeError("create failed")
        return self.root / lesson.lesson_id

    def lesson_dir(self, lesson: Lesson) -> Path:
        self.events.append("lesson.dir")
        return self.root / lesson.lesson_id

    def save_state(self, lesson: Lesson, *fields: str, **_kwargs: object) -> Lesson:
        self.events.append(f"lesson.save:{lesson.status.value}")
        self.saved.append((lesson.status, fields, lesson.error))
        return lesson


class FakeRecorder:
    def __init__(
        self,
        events: list[str],
        *,
        fail_start: bool = False,
        become_active_before_failure: bool = False,
    ) -> None:
        self.events = events
        self.fail_start = fail_start
        self.become_active_before_failure = become_active_before_failure
        self._active = False
        self.start_args: tuple[Path, int, object] | None = None
        self.stop_calls = 0

    @property
    def active(self) -> bool:
        return self._active

    def start(self, output_dir: Path, mic_device: int, system_source: object) -> None:
        self.events.append("recorder.start")
        self.start_args = (output_dir, mic_device, system_source)
        if self.fail_start:
            self._active = self.become_active_before_failure
            raise RuntimeError("capture boom")
        self._active = True

    def stop(self) -> object:
        self.events.append("recorder.stop")
        self.stop_calls += 1
        self._active = False
        return object()


def make_lesson() -> Lesson:
    return Lesson(
        student=Student(id="student", full_name="Student"),
        subject="Математика",
        topic="Векторы",
        lesson_date=date(2026, 8, 17),
    )


def test_start_recording_establishes_session_in_historical_order(tmp_path: Path) -> None:
    events: list[str] = []
    pipeline = FakePipeline(tmp_path, events)
    activities = FakeActivities(events)
    recorder = FakeRecorder(events)

    def recorder_factory() -> FakeRecorder:
        events.append("recorder.create")
        return recorder

    use_case = StartRecordingUseCase(pipeline, activities, recorder_factory)
    lesson = make_lesson()
    source = object()

    started = use_case.start(lesson, mic_device=7, system_source=source)

    assert started.lesson is lesson
    assert started.recorder is recorder
    assert started.lease is activities.lease
    assert started.directory == tmp_path / lesson.lesson_id / "recording"
    assert lesson.status == JobStatus.RECORDING
    assert recorder.active
    assert recorder.start_args == (started.directory, 7, source)
    assert activities.calls == [("recording", lesson.lesson_id, timedelta(minutes=5))]
    assert events == [
        "lesson.create",
        "lease.acquire",
        "lesson.dir",
        "recorder.create",
        "lesson.save:recording",
        "recorder.start",
    ]
    assert not activities.lease.released


def test_capture_failure_rolls_back_active_recorder_lease_and_lesson(tmp_path: Path) -> None:
    events: list[str] = []
    pipeline = FakePipeline(tmp_path, events)
    activities = FakeActivities(events)
    recorder = FakeRecorder(
        events,
        fail_start=True,
        become_active_before_failure=True,
    )
    use_case = StartRecordingUseCase(pipeline, activities, lambda: recorder)
    lesson = make_lesson()

    with pytest.raises(RuntimeError, match="capture boom"):
        use_case.start(lesson, mic_device=1, system_source=object())

    assert recorder.stop_calls == 1
    assert not recorder.active
    assert activities.lease.released
    assert lesson.status == JobStatus.FAILED
    assert lesson.error == "capture boom"
    assert pipeline.saved[-1] == (JobStatus.FAILED, ("status", "error"), "capture boom")
    assert events[-3:] == ["recorder.stop", "lease.release", "lesson.save:failed"]


def test_lease_failure_marks_created_lesson_failed_without_constructing_recorder(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    pipeline = FakePipeline(tmp_path, events)
    activities = FakeActivities(events, fail=True)
    factory_calls = 0

    def recorder_factory() -> FakeRecorder:
        nonlocal factory_calls
        factory_calls += 1
        return FakeRecorder(events)

    use_case = StartRecordingUseCase(pipeline, activities, recorder_factory)
    lesson = make_lesson()

    with pytest.raises(RuntimeError, match="lease busy"):
        use_case.start(lesson, mic_device=1, system_source=object())

    assert factory_calls == 0
    assert lesson.status == JobStatus.FAILED
    assert lesson.error == "lease busy"
    assert events == ["lesson.create", "lease.acquire", "lesson.save:failed"]


def test_create_failure_has_no_compensation_side_effects(tmp_path: Path) -> None:
    events: list[str] = []
    pipeline = FakePipeline(tmp_path, events, fail_create=True)
    activities = FakeActivities(events)
    factory_calls = 0

    def recorder_factory() -> FakeRecorder:
        nonlocal factory_calls
        factory_calls += 1
        return FakeRecorder(events)

    use_case = StartRecordingUseCase(pipeline, activities, recorder_factory)
    lesson = make_lesson()

    with pytest.raises(RuntimeError, match="create failed"):
        use_case.start(lesson, mic_device=1, system_source=object())

    assert factory_calls == 0
    assert lesson.status == JobStatus.DRAFT
    assert not pipeline.saved
    assert events == ["lesson.create"]


def test_abort_compensates_successful_start_when_presentation_setup_fails(tmp_path: Path) -> None:
    events: list[str] = []
    pipeline = FakePipeline(tmp_path, events)
    activities = FakeActivities(events)
    recorder = FakeRecorder(events)
    use_case = StartRecordingUseCase(pipeline, activities, lambda: recorder)
    lesson = make_lesson()
    started = use_case.start(lesson, mic_device=3, system_source=object())

    use_case.abort(started, RuntimeError("presentation boom"))

    assert recorder.stop_calls == 1
    assert activities.lease.released
    assert lesson.status == JobStatus.FAILED
    assert lesson.error == "presentation boom"
    assert events[-3:] == ["recorder.stop", "lease.release", "lesson.save:failed"]


def test_production_entrypoint_uses_start_recording_application_use_case() -> None:
    source = Path("src/tutor_assistant/ui/audio_resilient_app.py").read_text(encoding="utf-8")

    assert "StartRecordingUseCase" in source
    assert "self.start_recording_use_case.start(" in source
    assert "self.start_recording_use_case.abort(" in source
    assert "super().start_recording()" not in source
