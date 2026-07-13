from datetime import date

import pytest

from tutor_assistant.config import AppConfig
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.pipeline import LessonPipeline


class FailingTranscriber:
    def transcribe(self, _audio, _output_dir):
        raise RuntimeError("model failure")


def test_transcription_failure_is_persisted(monkeypatch, tmp_path) -> None:
    config = AppConfig(workspace=tmp_path)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 13),
        topic="Функции",
    )
    pipeline.create(lesson)
    lesson.transition(JobStatus.RECORDED)
    pipeline.store.save(lesson)
    audio = tmp_path / "lesson.wav"
    audio.touch()
    monkeypatch.setattr(pipeline, "transcriber", lambda: FailingTranscriber())

    with pytest.raises(RuntimeError, match="model failure"):
        pipeline.transcribe(lesson, audio)

    restored = pipeline.store.get(lesson.lesson_id)
    assert restored.status == JobStatus.FAILED
    assert "model failure" in restored.error
