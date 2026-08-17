from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import date
from pathlib import Path

from tutor_assistant.application.recording_recovery import (
    RecordingRecoveryState,
    RecoverRecordingUseCase,
)
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.recording import RecordingResult
from tutor_assistant.ui.recording_recovery_app import MainWindow as ProductionMainWindow


def make_lesson(*, status: JobStatus = JobStatus.RECORDING) -> Lesson:
    lesson = Lesson(
        lesson_id="lesson-1",
        student=Student(id="student", full_name="Student"),
        subject="Математика",
        topic="Векторы",
        lesson_date=date(2026, 8, 17),
    )
    if status != JobStatus.DRAFT:
        lesson.transition(status, force=True)
    return lesson


def make_result(tmp_path: Path, *, name: str = "lesson.wav") -> RecordingResult:
    mixed = tmp_path / name
    mixed.parent.mkdir(parents=True, exist_ok=True)
    mixed.write_bytes(b"audio")
    return RecordingResult(
        microphone_file=tmp_path / "microphone.wav",
        system_file=tmp_path / "system.wav",
        mixed_file=mixed,
        session_file=tmp_path / "session.json",
        sync_report=tmp_path / "sync.json",
        quality_report=tmp_path / "quality.json",
    )


class Saver:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls: list[tuple[JobStatus, tuple[str, ...], str | None]] = []
        self.attempts = 0

    def __call__(self, lesson: Lesson, fields: tuple[str, ...]) -> object:
        self.attempts += 1
        if self.fail_first and self.attempts == 1:
            raise RuntimeError("database boom")
        self.calls.append((lesson.status, fields, lesson.error))
        return lesson


def build_use_case(
    *,
    result: RecordingResult,
    lesson: Lesson | None,
    saver: Saver,
    finalizer=None,
    recover_error: Exception | None = None,
) -> RecoverRecordingUseCase:
    def recoverer(_directory: Path) -> RecordingResult:
        if recover_error is not None:
            raise recover_error
        return result

    return RecoverRecordingUseCase(
        discoverer=lambda workspace: (workspace / "lesson-1" / "recording",),
        recoverer=recoverer,
        lesson_lookup=lambda lesson_id: lesson if lesson_id == "lesson-1" else None,
        lesson_saver=saver,
        result_finalizer=finalizer or (lambda recovered, _lesson: recovered),
    )


def test_discover_preserves_infrastructure_order(tmp_path: Path) -> None:
    first = tmp_path / "a" / "recording"
    second = tmp_path / "b" / "recording"
    use_case = RecoverRecordingUseCase(
        discoverer=lambda _workspace: (first, second),
        recoverer=lambda _directory: make_result(tmp_path),
        lesson_lookup=lambda _lesson_id: None,
        lesson_saver=lambda _lesson, _fields: None,
        result_finalizer=lambda result, _lesson: result,
    )

    assert use_case.discover(tmp_path) == (first, second)


def test_recovery_reconciles_audio_and_recording_lesson(tmp_path: Path) -> None:
    recording_dir = tmp_path / "lesson-1" / "recording"
    raw = make_result(recording_dir)
    readable = recording_dir / "Student_2026-08-17.wav"
    readable.write_bytes(b"audio")
    lesson = make_lesson()
    saver = Saver()
    finalizer_calls = 0

    def finalizer(result: RecordingResult, expected: Lesson) -> RecordingResult:
        nonlocal finalizer_calls
        finalizer_calls += 1
        assert expected is lesson
        return replace(result, mixed_file=readable)

    outcome = build_use_case(
        result=raw,
        lesson=lesson,
        saver=saver,
        finalizer=finalizer,
    ).recover(recording_dir)

    assert outcome.state == RecordingRecoveryState.RECOVERED
    assert outcome.result is not None
    assert outcome.result.mixed_file == readable
    assert outcome.lesson is lesson
    assert lesson.status == JobStatus.RECORDED
    assert lesson.source_audio_local == str(readable.resolve())
    assert lesson.error is None
    assert finalizer_calls == 1
    assert saver.calls == [
        (
            JobStatus.RECORDED,
            ("source_audio_local", "status", "error"),
            None,
        )
    ]


def test_missing_lesson_is_successful_audio_only_recovery(tmp_path: Path) -> None:
    recording_dir = tmp_path / "lesson-1" / "recording"
    result = make_result(recording_dir)
    saver = Saver()
    finalizer_calls = 0

    def finalizer(recovered: RecordingResult, _lesson: Lesson) -> RecordingResult:
        nonlocal finalizer_calls
        finalizer_calls += 1
        return recovered

    outcome = build_use_case(
        result=result,
        lesson=None,
        saver=saver,
        finalizer=finalizer,
    ).recover(recording_dir)

    assert outcome.state == RecordingRecoveryState.AUDIO_ONLY
    assert outcome.result is result
    assert outcome.lesson is None
    assert finalizer_calls == 0
    assert saver.calls == []


def test_low_level_recovery_failure_keeps_lesson_recoverable(tmp_path: Path) -> None:
    recording_dir = tmp_path / "lesson-1" / "recording"
    lesson = make_lesson()
    saver = Saver()
    outcome = build_use_case(
        result=make_result(recording_dir),
        lesson=lesson,
        saver=saver,
        recover_error=RuntimeError("chunks boom"),
    ).recover(recording_dir)

    assert outcome.state == RecordingRecoveryState.FAILED
    assert outcome.result is None
    assert "chunks boom" in (outcome.error or "")
    assert lesson.status == JobStatus.RECORDING
    assert saver.calls == []


def test_finalizer_failure_marks_unfinished_lesson_failed(tmp_path: Path) -> None:
    recording_dir = tmp_path / "lesson-1" / "recording"
    raw = make_result(recording_dir)
    lesson = make_lesson()
    saver = Saver()

    def fail_finalizer(_result: RecordingResult, _lesson: Lesson) -> RecordingResult:
        raise RuntimeError("rename boom")

    outcome = build_use_case(
        result=raw,
        lesson=lesson,
        saver=saver,
        finalizer=fail_finalizer,
    ).recover(recording_dir)

    assert outcome.state == RecordingRecoveryState.FAILED
    assert outcome.result is raw
    assert "rename boom" in (outcome.error or "")
    assert lesson.status == JobStatus.FAILED
    assert "rename boom" in (lesson.error or "")
    assert saver.calls[-1][0] == JobStatus.FAILED
    assert saver.calls[-1][1] == ("status", "error")


def test_recovery_persistence_failure_is_compensated_to_failed(tmp_path: Path) -> None:
    recording_dir = tmp_path / "lesson-1" / "recording"
    result = make_result(recording_dir)
    lesson = make_lesson()
    saver = Saver(fail_first=True)

    outcome = build_use_case(
        result=result,
        lesson=lesson,
        saver=saver,
    ).recover(recording_dir)

    assert outcome.state == RecordingRecoveryState.FAILED
    assert outcome.result is result
    assert "database boom" in (outcome.error or "")
    assert lesson.status == JobStatus.FAILED
    assert saver.calls[-1][0] == JobStatus.FAILED


def test_stale_recovery_does_not_roll_progressed_lesson_backwards(tmp_path: Path) -> None:
    recording_dir = tmp_path / "lesson-1" / "recording"
    result = make_result(recording_dir)
    lesson = make_lesson(status=JobStatus.REVIEW_REQUIRED)
    saver = Saver()

    outcome = build_use_case(
        result=result,
        lesson=lesson,
        saver=saver,
    ).recover(recording_dir)

    assert outcome.state == RecordingRecoveryState.RECOVERED
    assert lesson.status == JobStatus.REVIEW_REQUIRED
    assert lesson.source_audio_local == str(result.mixed_file.resolve())
    assert saver.calls == [
        (JobStatus.REVIEW_REQUIRED, ("source_audio_local",), None)
    ]


def test_recovery_application_module_is_qt_independent() -> None:
    import tutor_assistant.application.recording_recovery as module

    assert "PySide" not in inspect.getsource(module)


def test_production_recovery_does_not_delegate_to_legacy_session_mutation() -> None:
    offer_source = inspect.getsource(ProductionMainWindow._offer_next_recovery)
    ready_source = inspect.getsource(ProductionMainWindow._recovery_outcome_ready)

    assert "recover_recording_use_case.recover" in offer_source
    assert "super()._recovery_ready" not in ready_source
    assert 'session["status"] = "recovered"' not in ready_source


def test_gui_entrypoint_uses_recovery_application_adapter() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'tutor-assistant-gui = "tutor_assistant.ui.recording_recovery_app:main"' in pyproject
