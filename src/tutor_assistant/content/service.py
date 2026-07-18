from __future__ import annotations

import hashlib
import mimetypes
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

from ..domain import Lesson
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
    StudentContentRepository,
)


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
        if recording.is_dir():
            candidates.update(recording.glob("*.wav"))
        if lesson.source_audio_local:
            source = Path(lesson.source_audio_local)
            if source.is_file():
                candidates.add(source)
        indexed = 0
        for candidate in sorted(candidates):
            if not candidate.is_file():
                continue
            try:
                self._resolve_path(candidate)
            except ContentPathError:
                continue
            kind = AssetKind.AUDIO if candidate.suffix.casefold() == ".wav" else AssetKind.METADATA
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
