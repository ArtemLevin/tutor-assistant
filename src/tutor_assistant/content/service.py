from __future__ import annotations

import hashlib
import mimetypes
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from ..domain import JobStatus, Lesson
from .importing import (
    DuplicateImportError,
    ImportCancellationToken,
    ImportCancelledError,
    ImportValidationError,
    LessonImportRequest,
    LessonImportResult,
)
from .models import (
    AssetKind,
    IndexReport,
    LessonAsset,
    LessonContent,
    LessonFilters,
    LessonPage,
    TranscriptRevision,
    _validate_lesson_id,
)
from .repository import (
    ContentConflictError,
    ContentNotFoundError,
    DuplicateAssetError,
    StudentContentRepository,
)

AUDIO_IMPORT_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
TRANSCRIPT_IMPORT_SUFFIXES = {".txt", ".md", ".markdown"}
MAX_TRANSCRIPT_IMPORT_BYTES = 25 * 1024 * 1024


class ContentPathError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class StudentContentService:
    """The application boundary for student-content CRUD and legacy indexing."""

    def __init__(self, workspace: Path, database_path: Path | None = None) -> None:
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.repository = StudentContentRepository(
            database_path or self.workspace / "tutor-assistant.sqlite3"
        )

    def _resolve_path(self, path: Path | str) -> tuple[Path, str]:
        candidate = Path(path)
        windows_path = PureWindowsPath(str(path))
        if windows_path.drive and not candidate.is_absolute():
            raise ContentPathError(f"Путь выходит за пределы каталога данных: {path}")
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(self.workspace).as_posix()
        except ValueError as exc:
            raise ContentPathError(f"Путь выходит за пределы каталога данных: {path}") from exc
        return resolved, relative

    def _lesson_json_path(self, lesson_id: str) -> Path:
        _validate_lesson_id(lesson_id)
        return self.workspace / "lessons" / lesson_id / "lesson.json"

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)

    def create_lesson(self, lesson: Lesson) -> Lesson:
        if self.repository.get_lesson(lesson.lesson_id, include_deleted=True):
            raise ContentConflictError(f"Занятие уже существует: {lesson.lesson_id}")
        self._atomic_write(self._lesson_json_path(lesson.lesson_id), lesson.model_dump_json(indent=2))
        self.repository.upsert_lesson(lesson)
        return lesson

    def update_lesson(self, lesson: Lesson) -> Lesson:
        if not self.repository.get_lesson(lesson.lesson_id, include_deleted=True):
            raise ContentNotFoundError(f"Занятие не найдено: {lesson.lesson_id}")
        lesson.updated_at = datetime.now(UTC)
        self._atomic_write(self._lesson_json_path(lesson.lesson_id), lesson.model_dump_json(indent=2))
        self.repository.upsert_lesson(lesson)
        return lesson

    def get_lesson(self, lesson_id: str, *, include_deleted: bool = False) -> LessonContent:
        return self.repository.get_content(lesson_id, include_deleted=include_deleted)

    def list_lessons(self, filters: LessonFilters | None = None) -> LessonPage:
        return self.repository.list_lessons(filters)

    def delete_lesson(self, lesson_id: str) -> None:
        self.repository.set_lesson_deleted(lesson_id, deleted=True)

    def restore_lesson(self, lesson_id: str) -> None:
        self.repository.set_lesson_deleted(lesson_id, deleted=False)

    def import_lesson(
        self,
        request: LessonImportRequest,
        *,
        cancellation: ImportCancellationToken | None = None,
        progress: Callable[[str, int], None] | None = None,
    ) -> LessonImportResult:
        """Stage, validate and atomically register a manually created lesson."""

        token = cancellation or ImportCancellationToken()
        lesson_id = _validate_lesson_id(request.lesson_id or uuid4().hex)
        subject = request.subject.strip()
        topic = request.topic.strip()
        if not subject:
            raise ImportValidationError("Укажите предмет")
        if not topic:
            raise ImportValidationError("Укажите тему занятия")
        audio_source = self._validate_import_source(
            request.audio_source,
            "аудиофайл",
            AUDIO_IMPORT_SUFFIXES,
        )
        transcript_source = self._validate_import_source(
            request.transcript_source,
            "транскрипт",
            TRANSCRIPT_IMPORT_SUFFIXES,
            max_bytes=MAX_TRANSCRIPT_IMPORT_BYTES,
        )
        if request.enqueue_audio and audio_source is None:
            raise ImportValidationError("Для постановки в очередь выберите аудиофайл")
        if request.enqueue_audio and transcript_source is not None:
            raise ImportValidationError(
                "Нельзя одновременно импортировать готовый транскрипт "
                "и ставить аудио в очередь"
            )
        if self.repository.get_lesson(lesson_id, include_deleted=True):
            raise ContentConflictError(f"Занятие уже существует: {lesson_id}")

        final_directory = self.workspace / "lessons" / lesson_id
        staging_root = self.workspace / ".import-staging"
        staging_directory = staging_root / f"{lesson_id}-{uuid4().hex}"
        moved_to_final = False
        audio_sha256: str | None = None
        audio_target: Path | None = None
        transcript_revision: TranscriptRevision | None = None

        def report(message: str, percent: int) -> None:
            if progress:
                try:
                    progress(message, max(0, min(100, percent)))
                except Exception:
                    pass

        try:
            token.check()
            if final_directory.exists():
                raise ContentConflictError(f"Каталог занятия уже существует: {lesson_id}")
            staging_directory.mkdir(parents=True, exist_ok=False)
            report("Проверяю исходные файлы…", 5)

            lesson = Lesson(
                lesson_id=lesson_id,
                student=request.student,
                subject=subject,
                lesson_date=request.lesson_date,
                topic=topic,
            )
            assets: list[LessonAsset] = []

            if audio_source:
                audio_relative = Path("lessons") / lesson_id / "recording" / (
                    f"imported_audio{audio_source.suffix.casefold()}"
                )
                staged_audio = staging_directory / "recording" / audio_relative.name
                audio_sha256, audio_size = self._copy_import_file(
                    audio_source,
                    staged_audio,
                    token,
                    lambda value: report("Копирую аудио в управляемое хранилище…", 10 + value // 2),
                )
                duplicate = self.repository.find_asset_by_sha256(
                    audio_sha256,
                    kind=AssetKind.AUDIO,
                )
                if duplicate:
                    raise DuplicateImportError(audio_sha256, duplicate.lesson_id)
                audio_target = self.workspace / audio_relative
                lesson.source_audio_local = str(audio_target.resolve())
                lesson.status = JobStatus.RECORDED
                assets.append(
                    LessonAsset(
                        lesson_id=lesson_id,
                        kind=AssetKind.AUDIO,
                        relative_path=audio_relative.as_posix(),
                        media_type=mimetypes.guess_type(audio_target.name)[0]
                        or "application/octet-stream",
                        size_bytes=audio_size,
                        sha256=audio_sha256,
                    )
                )

            if transcript_source:
                transcript_relative = (
                    Path("lessons") / lesson_id / "transcript" / "imported_transcript.txt"
                )
                staged_transcript = staging_directory / "transcript" / transcript_relative.name
                self._copy_import_file(
                    transcript_source,
                    staged_transcript,
                    token,
                    lambda value: report("Копирую транскрипт…", 60 + value // 5),
                )
                try:
                    content = staged_transcript.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeError) as exc:
                    raise ImportValidationError(f"Транскрипт должен быть текстом UTF-8: {exc}") from exc
                if "\x00" in content:
                    raise ImportValidationError("Транскрипт содержит бинарные данные")
                normalized = content.rstrip() + "\n"
                self._atomic_write(staged_transcript, normalized)
                transcript_sha256 = _sha256_text(normalized)
                transcript_target = self.workspace / transcript_relative
                lesson.artifacts.verified_transcript = str(transcript_target.resolve())
                lesson.status = JobStatus.REVIEW_REQUIRED
                assets.append(
                    LessonAsset(
                        lesson_id=lesson_id,
                        kind=AssetKind.TRANSCRIPT,
                        relative_path=transcript_relative.as_posix(),
                        media_type="text/plain",
                        size_bytes=staged_transcript.stat().st_size,
                        sha256=transcript_sha256,
                    )
                )
                transcript_revision = TranscriptRevision(
                    lesson_id=lesson_id,
                    revision_number=1,
                    relative_path=transcript_relative.as_posix(),
                    content=normalized,
                    content_sha256=transcript_sha256,
                    created_by="teacher-import",
                )

            token.check()
            lesson_json = staging_directory / "lesson.json"
            lesson.write_json(lesson_json)
            metadata_relative = Path("lessons") / lesson_id / "lesson.json"
            assets.append(
                LessonAsset(
                    lesson_id=lesson_id,
                    kind=AssetKind.METADATA,
                    relative_path=metadata_relative.as_posix(),
                    media_type="application/json",
                    size_bytes=lesson_json.stat().st_size,
                    sha256=_sha256_file(lesson_json),
                )
            )
            report("Фиксирую занятие…", 90)
            token.check()
            final_directory.parent.mkdir(parents=True, exist_ok=True)
            staging_directory.replace(final_directory)
            moved_to_final = True
            try:
                self.repository.import_lesson_bundle(lesson, assets, transcript_revision)
            except DuplicateAssetError as exc:
                shutil.rmtree(final_directory, ignore_errors=True)
                moved_to_final = False
                raise DuplicateImportError(exc.sha256, exc.lesson_id) from exc
            except Exception:
                shutil.rmtree(final_directory, ignore_errors=True)
                moved_to_final = False
                raise
            report("Импорт завершён", 100)
            stored_transcript = self.repository.current_transcript(lesson_id)
            return LessonImportResult(
                lesson=lesson,
                audio_path=audio_target,
                transcript=stored_transcript,
                audio_sha256=audio_sha256,
                enqueue_audio=bool(request.enqueue_audio and audio_target),
            )
        except ImportCancelledError:
            return LessonImportResult(cancelled=True)
        finally:
            if not moved_to_final:
                shutil.rmtree(staging_directory, ignore_errors=True)
            try:
                staging_root.rmdir()
            except OSError:
                pass

    @staticmethod
    def _validate_import_source(
        source: Path | None,
        label: str,
        allowed_suffixes: set[str],
        *,
        max_bytes: int | None = None,
    ) -> Path | None:
        if source is None:
            return None
        resolved = source.resolve()
        if not resolved.is_file():
            raise ImportValidationError(f"Не найден {label}: {source}")
        if resolved.suffix.casefold() not in allowed_suffixes:
            formats = ", ".join(sorted(allowed_suffixes))
            raise ImportValidationError(f"Неподдерживаемый формат {label}: ожидается {formats}")
        size = resolved.stat().st_size
        if size <= 0:
            raise ImportValidationError(f"Пустой {label} нельзя импортировать")
        if max_bytes is not None and size > max_bytes:
            raise ImportValidationError(f"{label.capitalize()} превышает допустимый размер")
        return resolved

    @staticmethod
    def _copy_import_file(
        source: Path,
        destination: Path,
        token: ImportCancellationToken,
        progress: Callable[[int], None],
    ) -> tuple[str, int]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        total = source.stat().st_size
        copied = 0
        try:
            with source.open("rb") as input_file, temporary.open("xb") as output_file:
                while chunk := input_file.read(1024 * 1024):
                    token.check()
                    output_file.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                    progress(round(copied / total * 100))
            token.check()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return digest.hexdigest(), copied

    def register_asset(self, lesson_id: str, path: Path | str, *, kind: AssetKind) -> LessonAsset:
        if not self.repository.get_lesson(lesson_id, include_deleted=True):
            raise ContentNotFoundError(f"Занятие не найдено: {lesson_id}")
        resolved, relative = self._resolve_path(path)
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        return self.repository.upsert_asset(
            LessonAsset(
                lesson_id=lesson_id,
                kind=kind,
                relative_path=relative,
                media_type=media_type,
                size_bytes=resolved.stat().st_size,
                sha256=_sha256_file(resolved),
            )
        )

    def delete_asset(self, asset_id: int) -> None:
        self.repository.set_asset_deleted(asset_id, deleted=True)

    def restore_asset(self, asset_id: int) -> None:
        self.repository.set_asset_deleted(asset_id, deleted=False)

    def save_transcript(
        self,
        lesson_id: str,
        text: str,
        *,
        path: Path | str | None = None,
        created_by: str = "teacher",
    ) -> TranscriptRevision:
        lesson = self.repository.get_lesson(lesson_id, include_deleted=True)
        if lesson is None:
            raise ContentNotFoundError(f"Занятие не найдено: {lesson_id}")
        target = path or Path("lessons") / lesson_id / "transcript" / "transcript_verified.txt"
        resolved, relative = self._resolve_path(target)
        normalized = text.rstrip() + "\n"
        self._atomic_write(resolved, normalized)
        revision = self.repository.add_transcript_revision(
            TranscriptRevision(
                lesson_id=lesson_id,
                revision_number=1,
                relative_path=relative,
                content=normalized,
                content_sha256=_sha256_text(normalized),
                created_by=created_by,
            )
        )
        lesson.artifacts.verified_transcript = str(resolved)
        self.update_lesson(lesson)
        return revision

    def delete_transcript_revision(self, revision_id: int) -> None:
        self.repository.set_transcript_deleted(revision_id, deleted=True)

    def restore_transcript_revision(self, revision_id: int) -> None:
        self.repository.set_transcript_deleted(revision_id, deleted=False)

    def revert_transcript(self, revision_id: int, *, created_by: str = "teacher") -> TranscriptRevision:
        revision = self.repository.get_transcript_revision(revision_id, include_deleted=True)
        if revision is None:
            raise ContentNotFoundError(f"Версия транскрипта не найдена: {revision_id}")
        return self.save_transcript(
            revision.lesson_id,
            revision.content,
            path=revision.relative_path,
            created_by=created_by,
        )

    def index_existing_lessons(self) -> IndexReport:
        report = IndexReport()
        lessons_root = self.workspace / "lessons"
        if not lessons_root.is_dir():
            return report
        for directory in sorted(lessons_root.iterdir()):
            if not directory.is_dir():
                continue
            report.scanned_directories += 1
            lesson_json = directory / "lesson.json"
            if not lesson_json.is_file():
                report.skipped_directories += 1
                continue
            try:
                lesson = Lesson.read_json(lesson_json)
                if lesson.lesson_id != directory.name:
                    raise ValueError(
                        f"lesson_id {lesson.lesson_id!r} не совпадает с каталогом {directory.name!r}"
                    )
                self.repository.upsert_lesson(lesson)
                report.indexed_lessons += 1
                report.indexed_assets += self._index_lesson_assets(lesson, directory)
                report.indexed_transcripts += self._index_lesson_transcript(lesson, directory)
            except Exception as exc:
                report.errors.append(f"{directory.name}: {exc}")
        return report

    def _index_lesson_assets(self, lesson: Lesson, directory: Path) -> int:
        candidates: set[Path] = {directory / "lesson.json"}
        recording = directory / "recording"
        transcript_directory = directory / "transcript"
        for content_directory in (recording, transcript_directory):
            if content_directory.is_dir():
                candidates.update(
                    candidate for candidate in content_directory.iterdir() if candidate.is_file()
                )
        if lesson.source_audio_local:
            source = Path(lesson.source_audio_local)
            if source.is_file():
                candidates.add(source)
        known_paths = [
            *lesson.artifacts.model_dump().values(),
            lesson.latex.tex_path,
            lesson.latex.pdf_path,
            lesson.latex.report_path,
            *lesson.latex.preview_paths,
        ]
        for value in known_paths:
            if not value:
                continue
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = self.workspace / candidate
            if candidate.is_file():
                candidates.add(candidate)
        indexed = 0
        for candidate in sorted(candidates):
            if not candidate.is_file():
                continue
            try:
                self._resolve_path(candidate)
            except ContentPathError:
                continue
            if candidate.suffix.casefold() == ".wav":
                kind = AssetKind.AUDIO
            elif candidate.parent == transcript_directory and candidate.suffix.casefold() == ".txt":
                kind = AssetKind.TRANSCRIPT
            elif candidate.suffix.casefold() == ".json":
                kind = AssetKind.METADATA
            else:
                kind = AssetKind.DOCUMENT
            self.register_asset(lesson.lesson_id, candidate, kind=kind)
            indexed += 1
        return indexed

    def _index_lesson_transcript(self, lesson: Lesson, directory: Path) -> int:
        candidates = [
            lesson.artifacts.verified_transcript,
            lesson.artifacts.cleaned_transcript,
            str(directory / "transcript" / "03_content_only_medium.txt"),
        ]
        seen: set[Path] = set()
        for value in candidates:
            if not value:
                continue
            candidate = Path(value)
            if not candidate.is_absolute():
                workspace_candidate = self.workspace / candidate
                candidate = workspace_candidate if workspace_candidate.exists() else directory / candidate
            try:
                candidate = candidate.resolve()
                candidate.relative_to(self.workspace)
            except ValueError:
                continue
            if candidate in seen or not candidate.is_file():
                continue
            seen.add(candidate)
            _, relative = self._resolve_path(candidate)
            content = candidate.read_text(encoding="utf-8")
            self.repository.add_transcript_revision(
                TranscriptRevision(
                    lesson_id=lesson.lesson_id,
                    revision_number=1,
                    relative_path=relative,
                    content=content,
                    content_sha256=_sha256_text(content),
                    created_by="legacy-indexer",
                ),
                deduplicate=True,
            )
            return 1
        return 0
