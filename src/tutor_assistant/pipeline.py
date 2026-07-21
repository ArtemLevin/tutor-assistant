from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .atomic_io import atomic_write_text
from .config import AppConfig
from .content import StudentContentService
from .domain import ArtifactPaths, JobStatus, Lesson, PublicationInfo
from .latex.remote import (
    LatexCompilationReservation,
    RemoteCompilationResult,
    RemoteLatexService,
    RemoteTexProbe,
)
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

    @staticmethod
    def _clear_latex_reservation(lesson: Lesson) -> None:
        lesson.latex.active_operation_id = None
        lesson.latex.active_tex_blob_sha = None
        lesson.latex.active_source_commit = None
        lesson.latex.active_branch = None
        lesson.latex.active_started_at = None

    def reserve_remote_latex(
        self,
        lesson: Lesson,
        probe: RemoteTexProbe,
        *,
        force: bool = False,
    ) -> LatexCompilationReservation | None:
        with self.content_service.activity(
            "latex-reservation",
            lesson_id=lesson.lesson_id,
            exclusive=True,
        ):
            content = self.content_service.get_lesson(lesson.lesson_id)
            current = content.lesson
            now = datetime.now(UTC)
            if current.latex.active_operation_id:
                started = current.latex.active_started_at
                timeout = timedelta(minutes=self.config.latex.reservation_timeout_minutes)
                if started is None or now - started < timeout:
                    return None
                self._clear_latex_reservation(current)
                if current.status == JobStatus.COMPILING_PDF:
                    current.transition(
                        JobStatus.COMPILE_FAILED,
                        "Предыдущая компиляция была прервана до сохранения результата",
                        force=True,
                    )
            if (
                not force
                and probe.blob_sha == current.latex.tex_blob_sha
                and current.status
                in {
                    JobStatus.COMPILE_FAILED,
                    JobStatus.PDF_REVIEW_REQUIRED,
                }
            ):
                return None
            if not force and current.latex.attempt >= self.config.latex.max_attempts:
                return None
            if current.status in {JobStatus.PUBLISHED, JobStatus.COMPILE_FAILED}:
                current.transition(JobStatus.GENERATED_TEX)
            elif current.status not in {JobStatus.GENERATED_TEX, JobStatus.COMPILING_PDF}:
                current.transition(JobStatus.GENERATED_TEX, force=True)
            if current.status != JobStatus.COMPILING_PDF:
                current.transition(JobStatus.COMPILING_PDF)
            operation_id = uuid4().hex
            current.latex.attempt += 1
            current.latex.tex_path = probe.path
            current.latex.active_operation_id = operation_id
            current.latex.active_tex_blob_sha = probe.blob_sha
            current.latex.active_source_commit = probe.remote_head
            current.latex.active_branch = probe.branch
            current.latex.active_started_at = now
            stored = self.save_state(
                current,
                "latex",
                "status",
                "error",
                force_status=True,
                expected_row_version=content.row_version,
            )
            row_version = self.content_service.repository.lesson_row_version(stored.lesson_id)
            logging.info(
                "LaTeX reserved: lesson=%s operation=%s blob=%s",
                stored.lesson_id,
                operation_id,
                probe.blob_sha,
            )
            return LatexCompilationReservation(
                operation_id=operation_id,
                lesson=stored.model_copy(deep=True),
                row_version=row_version,
                probe=probe,
            )

    def finalize_remote_latex(
        self,
        reservation: LatexCompilationReservation,
        *,
        result: RemoteCompilationResult | None = None,
        error: str | None = None,
    ) -> RemoteCompilationResult | None:
        with self.content_service.activity(
            "latex-finalize",
            lesson_id=reservation.lesson.lesson_id,
            exclusive=True,
        ):
            content = self.content_service.get_lesson(reservation.lesson.lesson_id)
            current = content.lesson
            if current.latex.active_operation_id != reservation.operation_id:
                logging.warning(
                    "Stale LaTeX finalize ignored: lesson=%s operation=%s active=%s",
                    current.lesson_id,
                    reservation.operation_id,
                    current.latex.active_operation_id,
                )
                return None
            if result is not None:
                compiled = result.lesson
                current.latex.pdf_path = compiled.latex.pdf_path
                current.latex.report_path = compiled.latex.report_path
                current.latex.preview_paths = list(compiled.latex.preview_paths)
                current.latex.tex_path = reservation.probe.path
                current.latex.tex_blob_sha = reservation.probe.blob_sha
                current.transition(compiled.status, compiled.error, force=True)
            else:
                current.latex.tex_path = reservation.probe.path
                current.latex.tex_blob_sha = reservation.probe.blob_sha
                current.transition(
                    JobStatus.COMPILE_FAILED,
                    error or "LaTeX-компиляция завершилась с ошибкой",
                    force=True,
                )
            self._clear_latex_reservation(current)
            stored = self.save_state(
                current,
                "latex",
                "status",
                "error",
                force_status=True,
                expected_row_version=content.row_version,
            )
            logging.info(
                "LaTeX finalized: lesson=%s operation=%s status=%s",
                stored.lesson_id,
                reservation.operation_id,
                stored.status.value,
            )
            if result is None:
                return None
            result.lesson = stored
            return result

    def compile_remote_latex(
        self,
        lesson: Lesson,
        *,
        force: bool = False,
        cache_dir: Path | None = None,
    ) -> RemoteCompilationResult:
        service = RemoteLatexService(self.config.repository, self.config.latex)
        probe = service.probe_lesson(lesson)
        if probe is None:
            raise FileNotFoundError("В ветке занятия отсутствует handbook/*.tex")
        reservation = self.reserve_remote_latex(lesson, probe, force=force)
        if reservation is None:
            raise RuntimeError("LaTeX-компиляция уже выполняется или версия уже обработана")
        try:
            result = service.compile_reserved(
                reservation,
                cache_dir=cache_dir or self.lesson_dir(reservation.lesson) / "latex-cache",
            )
        except Exception as exc:
            self.finalize_remote_latex(reservation, error=str(exc))
            raise
        finalized = self.finalize_remote_latex(reservation, result=result)
        if finalized is None:
            raise RuntimeError("Результат LaTeX устарел до финализации")
        self._replace_lesson(lesson, finalized.lesson)
        return finalized

    def scan_remote_latex(self) -> RemoteCompilationResult | None:
        service = RemoteLatexService(self.config.repository, self.config.latex)
        for lesson in self.store.list():
            if not service.is_candidate(lesson):
                continue
            probe = service.probe_lesson(lesson)
            if probe is None:
                continue
            reservation = self.reserve_remote_latex(lesson, probe)
            if reservation is None:
                continue
            try:
                result = service.compile_reserved(
                    reservation,
                    cache_dir=self.lesson_dir(reservation.lesson) / "latex-cache",
                )
            except Exception as exc:
                self.finalize_remote_latex(reservation, error=str(exc))
                raise
            finalized = self.finalize_remote_latex(reservation, result=result)
            if finalized is not None:
                return finalized
        return None

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
