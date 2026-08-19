import json
from datetime import date

import pytest

from tutor_assistant.config import AppConfig
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.pipeline import LessonPipeline
from tutor_assistant.transcription import TranscriptionResult


class FailingTranscriber:
    def transcribe(self, _audio, _output_dir):
        raise RuntimeError("model failure")


class DurableTranscriber:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio, output_dir):
        self.calls += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        raw = output_dir / "00_raw_fake.txt"
        timestamped = output_dir / "00_raw_timestamped.txt"
        cleaned = output_dir / "03_content_only_medium.txt"
        segments = output_dir / "00_raw_segments.json"
        signals = output_dir / "important_student_signals.json"
        manifest = output_dir / "manifest.json"
        raw.write_text("raw transcript", encoding="utf-8")
        timestamped.write_text("[00.00 — 01.00] raw transcript", encoding="utf-8")
        cleaned.write_text("clean transcript", encoding="utf-8")
        segments.write_text('[{"start": 0, "end": 1, "text": "raw transcript"}]', encoding="utf-8")
        signals.write_text("[]", encoding="utf-8")
        manifest.write_text(
            json.dumps(
                {
                    "provider": "fake",
                    "model": "fake-model",
                    "sources": [{"source_audio": str(audio.resolve())}],
                }
            ),
            encoding="utf-8",
        )
        return TranscriptionResult(
            output_dir=output_dir,
            raw=raw,
            timestamped=timestamped,
            cleaned=cleaned,
            segments=segments,
            signals=signals,
            manifest=manifest,
        )


def _recorded_lesson(pipeline: LessonPipeline) -> Lesson:
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 13),
        topic="Функции",
    )
    pipeline.create(lesson)
    lesson.transition(JobStatus.RECORDED)
    pipeline.save_state(lesson, "status", "error")
    return lesson


def test_transcription_failure_is_persisted(monkeypatch, tmp_path) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(pipeline)
    audio = tmp_path / "lesson.wav"
    audio.touch()
    monkeypatch.setattr(pipeline, "transcriber", lambda: FailingTranscriber())

    with pytest.raises(RuntimeError, match="model failure"):
        pipeline.transcribe(lesson, audio)

    restored = pipeline.store.get(lesson.lesson_id)
    assert restored.status == JobStatus.FAILED
    assert "model failure" in restored.error


def test_final_persistence_failure_reconciles_without_second_asr(monkeypatch, tmp_path) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = _recorded_lesson(pipeline)
    audio = tmp_path / "lesson.wav"
    audio.write_bytes(b"audio")
    transcriber = DurableTranscriber()
    monkeypatch.setattr(pipeline, "transcriber", lambda: transcriber)

    original_save_state = pipeline.save_state
    calls = 0

    def fail_final_save(current, *fields, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("database unavailable after artifacts")
        return original_save_state(current, *fields, **kwargs)

    monkeypatch.setattr(pipeline, "save_state", fail_final_save)

    with pytest.raises(RuntimeError, match="database unavailable after artifacts"):
        pipeline.transcribe(lesson, audio)

    assert transcriber.calls == 1
    persisted = pipeline.content_service.get_lesson(lesson.lesson_id).lesson
    assert persisted.status == JobStatus.TRANSCRIBING

    recovered = pipeline.transcribe(persisted, audio)

    assert transcriber.calls == 1
    assert recovered.status == JobStatus.REVIEW_REQUIRED
    stored = pipeline.content_service.get_lesson(lesson.lesson_id).lesson
    assert stored.status == JobStatus.REVIEW_REQUIRED
    assert stored.artifacts.transcription_manifest
