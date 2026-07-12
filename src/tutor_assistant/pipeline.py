from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import AppConfig
from .domain import ArtifactPaths, JobStatus, Lesson, PublicationInfo
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
            transcriber = WhisperTranscriber(self.config.whisper)
            recording_dir = audio.parent
            microphone = recording_dir / "microphone.wav"
            system = recording_dir / "system.wav"
            sync_report = recording_dir / "sync_report.json"
            if self.config.recording.dual_channel_transcription and microphone.is_file() and system.is_file():
                sync = json.loads(sync_report.read_text(encoding="utf-8")) if sync_report.exists() else {}
                result = transcriber.transcribe_dual(
                    microphone,
                    system,
                    directory / "transcript",
                    microphone_offset_seconds=float(sync.get("microphone_delay_ms", 0)) / 1000,
                    system_offset_seconds=float(sync.get("system_delay_ms", 0)) / 1000,
                )
            else:
                result = transcriber.transcribe(audio, directory / "transcript")
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
                teacher_transcript=str(result.teacher_transcript.resolve())
                if result.teacher_transcript
                else None,
                student_transcript=str(result.student_transcript.resolve())
                if result.student_transcript
                else None,
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
        lesson.publication = PublicationInfo(
            branch=target.branch,
            repository_path=target.repository_path,
            commit=target.commit,
            pr_url=target.pr_url,
            warnings=list(target.warnings),
        )
        lesson.write_json(self.lesson_dir(lesson) / "lesson.json")
        self.store.save(lesson)
        return target
