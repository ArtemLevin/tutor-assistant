from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path

from .atomic_io import atomic_write_text
from .config import AppConfig
from .content import StudentContentService
from .domain import ArtifactPaths, JobStatus, Lesson, PublicationInfo
from .publisher import LessonPublisher, PublicationResult
from .store import LessonStore
from .transcription import WhisperTranscriber


class LessonPipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = LessonStore(config.workspace / "tutor-assistant.sqlite3")
        self.content_service = StudentContentService(
            config.workspace,
            self.store.path,
            trash_retention_days=config.content.trash_retention_days,
        )
        self._transcriber: WhisperTranscriber | None = None

    def transcriber(self) -> WhisperTranscriber:
        if self._transcriber is None:
            self._transcriber = WhisperTranscriber(self.config.whisper)
        return self._transcriber

    def lesson_dir(self, lesson: Lesson) -> Path:
        return self.config.workspace / "lessons" / lesson.lesson_id

    def create(self, lesson: Lesson) -> Path:
        stored = self.content_service.create_lesson(lesson)
        self._replace_lesson(lesson, stored)
        return self.lesson_dir(stored)

    @staticmethod
    def _replace_lesson(target: Lesson, source: Lesson) -> None:
        for field in Lesson.model_fields:
            setattr(target, field, deepcopy(getattr(source, field)))

    def save_state(
        self,
        lesson: Lesson,
        *fields: str,
        force_status: bool = False,
        expected_row_version: int | None = None,
    ) -> Lesson:
        if (
            self.content_service.repository.get_lesson(
                lesson.lesson_id,
                include_deleted=True,
            )
            is None
        ):
            self.content_service.create_lesson(lesson)
        stored = self.content_service.persist_pipeline_lesson(
            lesson,
            frozenset(fields),
            force_status=force_status,
            expected_row_version=expected_row_version,
        )
        self._replace_lesson(lesson, stored)
        return stored

    def transcribe(self, lesson: Lesson, audio: Path) -> Lesson:
        with self.content_service.activity("transcription", lesson_id=lesson.lesson_id):
            return self._transcribe(lesson, audio)

    def _transcribe(self, lesson: Lesson, audio: Path) -> Lesson:
        directory = self.lesson_dir(lesson)
        try:
            lesson.transition(JobStatus.TRANSCRIBING)
            lesson = self.save_state(lesson, "status", "error")
            transcriber = self.transcriber()
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
            atomic_write_text(
                verified,
                result.cleaned.read_text(encoding="utf-8"),
            )
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
            try:
                lesson = self.save_state(lesson, "status", "error")
            except Exception:
                logging.exception("Не удалось сохранить состояние ошибки транскрибации")
            raise
        else:
            lesson = self.save_state(
                lesson,
                "source_audio_local",
                "artifacts",
                "status",
                "error",
            )
        return lesson

    def approve_transcript(self, lesson: Lesson, text: str) -> None:
        if not lesson.artifacts.verified_transcript:
            raise RuntimeError("Файл транскрипта отсутствует")
        path = Path(lesson.artifacts.verified_transcript)
        if not path.is_file():
            raise RuntimeError(f"Файл транскрипта не найден: {path}")
        self.content_service.save_transcript(
            lesson.lesson_id,
            text,
            path=path,
            created_by="teacher-review",
        )
        current = self.content_service.get_lesson(lesson.lesson_id).lesson
        current.transition(JobStatus.READY)
        stored = self.save_state(current, "status", "error")
        self._replace_lesson(lesson, stored)

    def publish(self, lesson: Lesson) -> PublicationResult:
        with self.content_service.activity("publication", lesson_id=lesson.lesson_id):
            return self._publish(lesson)

    def _publish(self, lesson: Lesson) -> PublicationResult:
        content = self.content_service.get_lesson(lesson.lesson_id)
        current = content.lesson
        target = LessonPublisher(self.config.repository).publish(
            current,
            self.lesson_dir(current),
        )
        current.publication = PublicationInfo(
            branch=target.branch,
            repository_path=target.repository_path,
            commit=target.commit,
            pr_url=target.pr_url,
            warnings=list(target.warnings),
        )
        stored = self.save_state(
            current,
            "publication",
            "status",
            "error",
            expected_row_version=content.row_version,
        )
        self._replace_lesson(lesson, stored)
        return target
