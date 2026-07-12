from __future__ import annotations

import shutil
from pathlib import Path

from .config import AppConfig
from .domain import ArtifactPaths, JobStatus, Lesson
from .publisher import LessonPublisher, PublicationResult
from .store import LessonStore
from .transcription import WhisperTranscriber


class LessonPipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = LessonStore(config.workspace / "tutor-assistant.sqlite3")

    def lesson_dir(self, lesson: Lesson) -> Path:
        return self.config.workspace / "lessons" / lesson.lesson_id

    def create(self, lesson: Lesson) -> Path:
        directory = self.lesson_dir(lesson)
        directory.mkdir(parents=True, exist_ok=True)
        lesson.write_json(directory / "lesson.json")
        self.store.save(lesson)
        return directory

    def transcribe(self, lesson: Lesson, audio: Path) -> Lesson:
        lesson.transition(JobStatus.TRANSCRIBING)
        self.store.save(lesson)
        directory = self.lesson_dir(lesson)
        try:
            result = WhisperTranscriber(self.config.whisper).transcribe(audio, directory / "transcript")
            verified = directory / "transcript" / "transcript_verified.txt"
            shutil.copy2(result.cleaned, verified)
            lesson.source_audio_local = str(audio.resolve())
            lesson.artifacts = ArtifactPaths(
                raw_transcript=str(result.raw.resolve()),
                timestamped_transcript=str(result.timestamped.resolve()),
                cleaned_transcript=str(result.cleaned.resolve()),
                verified_transcript=str(verified.resolve()),
                segments_json=str(result.segments.resolve()),
                student_signals=str(result.signals.resolve()),
                transcription_manifest=str(result.manifest.resolve()),
            )
            lesson.transition(JobStatus.REVIEW_REQUIRED)
        except Exception as exc:
            lesson.transition(JobStatus.FAILED, str(exc))
            raise
        finally:
            lesson.write_json(directory / "lesson.json")
            self.store.save(lesson)
        return lesson

    def approve_transcript(self, lesson: Lesson, text: str) -> None:
        if not lesson.artifacts.verified_transcript:
            raise RuntimeError("Файл транскрипта отсутствует")
        path = Path(lesson.artifacts.verified_transcript)
        if not path.is_file():
            raise RuntimeError(f"Файл транскрипта не найден: {path}")
        path.write_text(text.strip() + "\n", encoding="utf-8")
        lesson.transition(JobStatus.READY)
        lesson.write_json(self.lesson_dir(lesson) / "lesson.json")
        self.store.save(lesson)

    def publish(self, lesson: Lesson) -> PublicationResult:
        target = LessonPublisher(self.config.repository).publish(lesson, self.lesson_dir(lesson))
        self.store.save(lesson)
        return target
