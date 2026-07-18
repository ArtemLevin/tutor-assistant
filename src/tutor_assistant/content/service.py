from __future__ import annotations

import hashlib
import logging
import mimetypes
import shutil
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from difflib import unified_diff
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from ..atomic_io import atomic_write_text
from ..domain import JobStatus, Lesson, Student
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
    ContentIntegrityIssue,
    ContentIntegrityReport,
    ContentOperationKind,
    IndexReport,
    IntegritySeverity,
    LessonAsset,
    LessonContent,
    LessonFilters,
    LessonPage,
    StorageUsage,
    TemporaryCleanupResult,
    TranscriptDraft,
    TranscriptRevision,
    TrashActionResult,
    TrashEntry,
    TrashState,
    TrashSummary,
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
_EXPECTED_REVISION_UNSET = object()


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

    def __init__(
        self,
        workspace: Path,
        database_path: Path | None = None,
        *,
        trash_retention_days: int = 30,
    ) -> None:
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not 0 <= trash_retention_days <= 3650:
            raise ValueError("Срок хранения корзины должен быть от 0 до 3650 дней")
        self.trash_retention_days = trash_retention_days
        self.repository = StudentContentRepository(
            database_path or self.workspace / "tutor-assistant.sqlite3"
        )
        self.recover_trash_operations()
        self.recover_file_sync()
        fts_enabled, fts_documents = self.repository.search_index_status()
        if fts_enabled and fts_documents != len(self.repository.list_lesson_index_states()):
            self.repository.rebuild_search_index()

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

    def _managed_path_for_lesson(self, lesson_id: str, relative_path: str) -> Path:
        content = self.repository.get_content(lesson_id, include_deleted=True)
        if content.deleted_at:
            trash = self.repository.get_trash_entry(lesson_id)
            if trash is None:
                raise ContentNotFoundError(f"Удалённое занятие не связано с корзиной: {lesson_id}")
            relative = Path(relative_path)
            lesson_prefix = Path("lessons") / lesson_id
            try:
                suffix = relative.relative_to(lesson_prefix)
            except ValueError as exc:
                raise ContentPathError(f"Путь не принадлежит занятию {lesson_id}: {relative_path}") from exc
            return self.workspace / trash.trash_relative_path / suffix
        resolved, _relative = self._resolve_path(relative_path)
        return resolved

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        atomic_write_text(path, text)

    def _synchronize_lesson_files(self, lesson_id: str, *, project_assets: bool = True) -> int:
        content = self.repository.get_content(lesson_id, include_deleted=True)
        indexed_assets = 0
        try:
            if content.transcript:
                transcript_path = self._managed_path_for_lesson(
                    lesson_id,
                    content.transcript.relative_path,
                )
                self._atomic_write(transcript_path, content.transcript.content)
            lesson_json = self._managed_path_for_lesson(
                lesson_id,
                (Path("lessons") / lesson_id / "lesson.json").as_posix(),
            )
            self._atomic_write(lesson_json, content.lesson.model_dump_json(indent=2))
            if content.deleted_at is None:
                if project_assets:
                    indexed_assets = self._index_lesson_assets(
                        content.lesson,
                        self.workspace / "lessons" / lesson_id,
                    )
                else:
                    self.register_asset(lesson_id, lesson_json, kind=AssetKind.METADATA)
                    indexed_assets = 1
        except Exception as exc:
            self.repository.fail_file_sync(lesson_id, str(exc))
            raise
        self.repository.complete_file_sync(lesson_id)
        return indexed_assets

    def recover_file_sync(self) -> None:
        for lesson_id, _last_error in self.repository.pending_file_sync():
            try:
                self._synchronize_lesson_files(lesson_id)
            except ContentNotFoundError:
                self.repository.complete_file_sync(lesson_id)
            except Exception:
                logging.exception(
                    "Не удалось восстановить файлы занятия из SQLite: %s",
                    lesson_id,
                )

    def create_lesson(self, lesson: Lesson) -> Lesson:
        _validate_lesson_id(lesson.lesson_id)
        self.repository.insert_lesson(lesson)
        self._synchronize_lesson_files(lesson.lesson_id)
        return lesson

    def update_lesson(self, lesson: Lesson, *, expected_row_version: int) -> Lesson:
        _validate_lesson_id(lesson.lesson_id)
        updated = self.repository.replace_lesson(
            lesson,
            expected_row_version=expected_row_version,
        )
        self._synchronize_lesson_files(updated.lesson_id)
        for field in Lesson.model_fields:
            setattr(lesson, field, deepcopy(getattr(updated, field)))
        return updated

    def persist_pipeline_lesson(
        self,
        lesson: Lesson,
        fields: set[str] | frozenset[str],
        *,
        expected_row_version: int | None = None,
        force_status: bool = False,
    ) -> Lesson:
        _validate_lesson_id(lesson.lesson_id)
        updated = self.repository.update_pipeline_lesson(
            lesson,
            fields,
            expected_row_version=expected_row_version,
            force_status=force_status,
        )
        self._synchronize_lesson_files(
            updated.lesson_id,
            project_assets=bool(frozenset(fields) & {"source_audio_local", "artifacts", "latex"}),
        )
        return updated

    def update_lesson_metadata(
        self,
        lesson_id: str,
        *,
        student: Student,
        subject: str,
        lesson_date: date,
        topic: str,
        expected_updated_at: datetime,
        expected_row_version: int | None = None,
    ) -> Lesson:
        subject = subject.strip()
        topic = topic.strip()
        if not subject:
            raise ValueError("Укажите предмет")
        if not topic:
            raise ValueError("Укажите тему занятия")
        lesson = self.repository.update_lesson_metadata(
            lesson_id,
            student=student,
            subject=subject,
            lesson_date=lesson_date,
            topic=topic,
            expected_updated_at=expected_updated_at,
            expected_row_version=expected_row_version,
        )
        self._synchronize_lesson_files(lesson_id, project_assets=False)
        return lesson

    def get_lesson(self, lesson_id: str, *, include_deleted: bool = False) -> LessonContent:
        return self.repository.get_content(lesson_id, include_deleted=include_deleted)

    def list_lessons(self, filters: LessonFilters | None = None) -> LessonPage:
        return self.repository.list_lessons(filters)

    @staticmethod
    def _directory_size(path: Path) -> int:
        if not path.is_dir():
            return path.stat().st_size if path.is_file() else 0
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

    @staticmethod
    def _safe_path_size(path: Path) -> int:
        try:
            if path.is_symlink():
                return path.lstat().st_size
            if path.is_file():
                return path.stat().st_size
            if not path.is_dir():
                return 0
            total = 0
            for item in path.rglob("*"):
                try:
                    if item.is_symlink():
                        total += item.lstat().st_size
                    elif item.is_file():
                        total += item.stat().st_size
                except OSError:
                    continue
            return total
        except OSError:
            return 0

    def storage_usage(self) -> StorageUsage:
        database = self.repository.path
        database_bytes = sum(
            self._safe_path_size(Path(f"{database}{suffix}")) for suffix in ("", "-wal", "-shm")
        )
        try:
            free_bytes = shutil.disk_usage(self.workspace).free
        except OSError:
            free_bytes = 0
        return StorageUsage(
            lessons_bytes=self._safe_path_size(self.workspace / "lessons"),
            trash_bytes=self._safe_path_size(self.workspace / "trash"),
            temporary_bytes=(
                self._safe_path_size(self.workspace / ".import-staging")
                + self._safe_path_size(self.workspace / ".trash-purge")
            ),
            database_bytes=database_bytes,
            free_bytes=free_bytes,
        )

    def _temporary_candidates(
        self,
        *,
        now: datetime | None = None,
        minimum_age: timedelta = timedelta(hours=24),
    ) -> list[Path]:
        threshold = (now or datetime.now(UTC)).timestamp() - minimum_age.total_seconds()
        protected = self.repository.protected_temporary_paths()
        candidates: list[Path] = []

        for root_name in (".import-staging", ".trash-purge"):
            root = self.workspace / root_name
            if not root.is_dir():
                continue
            for child in root.iterdir():
                relative = child.relative_to(self.workspace).as_posix()
                if relative in protected:
                    continue
                try:
                    if child.lstat().st_mtime <= threshold:
                        candidates.append(child)
                except OSError:
                    continue

        lessons = self.workspace / "lessons"
        if lessons.is_dir():
            for candidate in lessons.rglob("*"):
                if not candidate.name.endswith((".tmp", ".part")):
                    continue
                try:
                    if candidate.lstat().st_mtime <= threshold:
                        candidates.append(candidate)
                except OSError:
                    continue
        return sorted(set(candidates), key=lambda path: path.as_posix().casefold())

    def cleanup_temporary_files(
        self,
        *,
        now: datetime | None = None,
        minimum_age: timedelta = timedelta(hours=24),
    ) -> TemporaryCleanupResult:
        result = TemporaryCleanupResult()
        for candidate in self._temporary_candidates(now=now, minimum_age=minimum_age):
            relative = candidate.relative_to(self.workspace).as_posix()
            size = self._safe_path_size(candidate)
            try:
                if candidate.is_symlink() or candidate.is_file():
                    candidate.unlink()
                elif candidate.is_dir():
                    resolved = candidate.resolve()
                    resolved.relative_to(self.workspace)
                    shutil.rmtree(resolved)
                else:
                    continue
                result.removed_paths.append(relative)
                result.released_bytes += size
                self._remove_empty_directory(candidate.parent)
            except (OSError, ValueError) as exc:
                result.errors.append(f"{relative}: {exc}")
        return result

    def inspect_content_integrity(self) -> ContentIntegrityReport:
        database_ok, database_message = self.repository.database_integrity_status()
        fts_enabled, fts_documents = self.repository.search_index_status()
        states = self.repository.list_lesson_index_states()
        indexed_ids = {lesson_id for lesson_id, _deleted in states}
        active_ids = {lesson_id for lesson_id, deleted in states if not deleted}
        deleted_ids = {lesson_id for lesson_id, deleted in states if deleted}
        trash_entries = {item.lesson.lesson_id: item.entry for item in self.repository.list_trash_items()}
        lesson_root = self.workspace / "lessons"
        trash_root = self.workspace / "trash" / "lessons"
        lesson_directories = (
            {path.name: path for path in lesson_root.iterdir() if path.is_dir()}
            if lesson_root.is_dir()
            else {}
        )
        trash_directories = (
            {path.name: path for path in trash_root.iterdir() if path.is_dir()} if trash_root.is_dir() else {}
        )
        issues: list[ContentIntegrityIssue] = []

        def issue(
            severity: IntegritySeverity,
            code: str,
            message: str,
            *,
            lesson_id: str | None = None,
            path: str | None = None,
        ) -> None:
            issues.append(
                ContentIntegrityIssue(
                    severity=severity,
                    code=code,
                    message=message,
                    lesson_id=lesson_id,
                    relative_path=path,
                )
            )

        if not database_ok:
            issue(IntegritySeverity.ERROR, "database", database_message)
        if fts_enabled and fts_documents != len(states):
            issue(
                IntegritySeverity.WARNING,
                "search_index",
                f"FTS содержит {fts_documents} документов вместо {len(states)}",
            )
        if not fts_enabled:
            issue(
                IntegritySeverity.INFO,
                "search_fallback",
                "SQLite FTS5 недоступен; используется совместимый линейный поиск",
            )

        orphan_directories = [
            path.relative_to(self.workspace).as_posix()
            for lesson_id, path in lesson_directories.items()
            if lesson_id not in indexed_ids
        ]
        orphan_directories.extend(
            path.relative_to(self.workspace).as_posix()
            for lesson_id, path in trash_directories.items()
            if lesson_id not in trash_entries
        )
        for relative in orphan_directories:
            issue(
                IntegritySeverity.WARNING,
                "orphan_directory",
                "Каталог не связан с записью SQLite и оставлен без изменений",
                path=relative,
            )

        for lesson_id in sorted(active_ids):
            directory = lesson_directories.get(lesson_id)
            if directory is None:
                issue(
                    IntegritySeverity.ERROR,
                    "missing_lesson_directory",
                    "Для активного занятия отсутствует управляемый каталог",
                    lesson_id=lesson_id,
                    path=f"lessons/{lesson_id}",
                )
                continue
            lesson_json = directory / "lesson.json"
            try:
                disk_lesson = Lesson.read_json(lesson_json)
                if disk_lesson.lesson_id != lesson_id:
                    raise ValueError("lesson_id не совпадает с именем каталога")
            except Exception as exc:
                issue(
                    IntegritySeverity.ERROR,
                    "invalid_lesson_json",
                    str(exc),
                    lesson_id=lesson_id,
                    path=lesson_json.relative_to(self.workspace).as_posix(),
                )
            try:
                content = self.repository.get_content(lesson_id)
            except Exception as exc:
                issue(IntegritySeverity.ERROR, "content_read", str(exc), lesson_id=lesson_id)
                continue
            for asset in content.assets:
                try:
                    absolute, relative = self._resolve_path(asset.relative_path)
                except ContentPathError as exc:
                    issue(
                        IntegritySeverity.ERROR,
                        "unsafe_path",
                        str(exc),
                        lesson_id=lesson_id,
                        path=asset.relative_path,
                    )
                    continue
                if not absolute.is_file():
                    issue(
                        IntegritySeverity.WARNING,
                        "missing_asset",
                        "Зарегистрированный файл отсутствует",
                        lesson_id=lesson_id,
                        path=relative,
                    )
                    continue
                try:
                    measured_size = absolute.stat().st_size
                    changed = measured_size != asset.size_bytes or _sha256_file(absolute) != asset.sha256
                except OSError as exc:
                    issue(
                        IntegritySeverity.ERROR,
                        "asset_read",
                        str(exc),
                        lesson_id=lesson_id,
                        path=relative,
                    )
                    continue
                if changed:
                    issue(
                        IntegritySeverity.WARNING,
                        "asset_changed",
                        "Размер или SHA-256 файла отличается от индекса",
                        lesson_id=lesson_id,
                        path=relative,
                    )
            if content.transcript:
                try:
                    transcript_path, relative = self._resolve_path(content.transcript.relative_path)
                    try:
                        changed = (
                            transcript_path.is_file()
                            and _sha256_file(transcript_path) != content.transcript.content_sha256
                        )
                    except OSError as exc:
                        issue(
                            IntegritySeverity.ERROR,
                            "transcript_read",
                            str(exc),
                            lesson_id=lesson_id,
                            path=relative,
                        )
                        continue
                    if changed:
                        issue(
                            IntegritySeverity.WARNING,
                            "transcript_changed",
                            "Файл транскрипта отличается от подтверждённой копии SQLite",
                            lesson_id=lesson_id,
                            path=relative,
                        )
                except ContentPathError as exc:
                    issue(
                        IntegritySeverity.ERROR,
                        "unsafe_transcript_path",
                        str(exc),
                        lesson_id=lesson_id,
                        path=content.transcript.relative_path,
                    )

        for lesson_id in sorted(deleted_ids):
            if lesson_id not in trash_entries:
                issue(
                    IntegritySeverity.ERROR,
                    "missing_trash_record",
                    "Удалённое занятие не связано с корзиной",
                    lesson_id=lesson_id,
                )
        for lesson_id, entry in trash_entries.items():
            path = self.workspace / entry.trash_relative_path
            if not path.is_dir():
                issue(
                    IntegritySeverity.ERROR,
                    "missing_trash_directory",
                    "Каталог занятия отсутствует в корзине",
                    lesson_id=lesson_id,
                    path=entry.trash_relative_path,
                )

        temporary_paths = [
            path.relative_to(self.workspace).as_posix() for path in self._temporary_candidates()
        ]
        return ContentIntegrityReport(
            database_ok=database_ok,
            database_message=database_message,
            fts_enabled=fts_enabled,
            fts_documents=fts_documents,
            indexed_lessons=len(states),
            lesson_directories=len(lesson_directories),
            trash_items=len(trash_entries),
            orphan_directories=sorted(orphan_directories),
            temporary_paths=temporary_paths,
            storage=self.storage_usage(),
            issues=issues,
        )

    def rebuild_search_index(self) -> int:
        return self.repository.rebuild_search_index()

    def set_trash_retention_days(self, days: int) -> None:
        if not 0 <= days <= 3650:
            raise ValueError("Срок хранения корзины должен быть от 0 до 3650 дней")
        self.repository.reschedule_trash_purge(days)
        self.trash_retention_days = days

    def delete_lesson(self, lesson_id: str) -> TrashActionResult:
        _validate_lesson_id(lesson_id)
        source_relative = Path("lessons") / lesson_id
        trash_relative = Path("trash") / "lessons" / lesson_id
        source = self.workspace / source_relative
        destination = self.workspace / trash_relative
        if destination.exists():
            raise ContentConflictError(f"Каталог уже существует в корзине: {lesson_id}")
        size_bytes = self._directory_size(source)
        now = datetime.now(UTC)
        operation_id = uuid4().hex
        entry = TrashEntry(
            lesson_id=lesson_id,
            original_relative_path=source_relative.as_posix(),
            trash_relative_path=trash_relative.as_posix(),
            size_bytes=size_bytes,
            state=TrashState.MOVING,
            deleted_at=now,
            purge_after=now + timedelta(days=self.trash_retention_days),
        )
        self.repository.begin_trash(entry, operation_id)
        moved = False
        try:
            if source.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.replace(destination)
                moved = True
            self.repository.complete_trash(lesson_id, operation_id)
        except Exception as exc:
            if not moved:
                self.repository.rollback_trash(lesson_id, operation_id, str(exc))
            raise
        return TrashActionResult(
            lesson_id=lesson_id,
            size_bytes=size_bytes,
            operation=ContentOperationKind.DELETE,
        )

    def restore_lesson(self, lesson_id: str) -> TrashActionResult:
        _validate_lesson_id(lesson_id)
        operation_id = uuid4().hex
        now = datetime.now(UTC)
        existing = self.repository.get_trash_entry(lesson_id)
        if existing is None:
            raise ContentNotFoundError(f"Занятие не найдено в корзине: {lesson_id}")
        source = self.workspace / existing.trash_relative_path
        destination = self.workspace / existing.original_relative_path
        if destination.exists():
            raise ContentConflictError(f"Каталог занятия уже существует: {lesson_id}")
        entry = self.repository.begin_restore(lesson_id, operation_id, now)
        moved = False
        try:
            if source.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.replace(destination)
                moved = True
            self.repository.complete_restore(lesson_id, operation_id)
        except Exception as exc:
            if not moved:
                self.repository.rollback_restore(lesson_id, operation_id, str(exc))
            raise
        return TrashActionResult(
            lesson_id=lesson_id,
            size_bytes=entry.size_bytes,
            operation=ContentOperationKind.RESTORE,
        )

    def permanently_delete_lesson(self, lesson_id: str) -> TrashActionResult:
        _validate_lesson_id(lesson_id)
        operation_id = uuid4().hex
        staging_relative = Path(".trash-purge") / operation_id
        existing = self.repository.get_trash_entry(lesson_id)
        if existing is None:
            raise ContentNotFoundError(f"Занятие не найдено в корзине: {lesson_id}")
        source = self.workspace / existing.trash_relative_path
        staging = self.workspace / staging_relative
        if staging.exists():
            raise ContentConflictError(f"Временный каталог очистки уже существует: {operation_id}")
        entry = self.repository.begin_purge(
            lesson_id,
            operation_id,
            staging_relative.as_posix(),
            datetime.now(UTC),
        )
        moved = False
        database_purged = False
        try:
            if source.exists():
                staging.parent.mkdir(parents=True, exist_ok=True)
                source.replace(staging)
                moved = True
            self.repository.complete_purge_database(lesson_id, operation_id)
            database_purged = True
            if staging.exists():
                shutil.rmtree(staging)
            self._remove_empty_directory(staging.parent)
            self.repository.complete_cleanup(operation_id)
        except Exception as exc:
            if not moved and not database_purged:
                self.repository.rollback_purge(lesson_id, operation_id, str(exc))
            raise
        return TrashActionResult(
            lesson_id=lesson_id,
            size_bytes=entry.size_bytes,
            operation=ContentOperationKind.PURGE,
        )

    def trash_summary(self, *, now: datetime | None = None) -> TrashSummary:
        current = now or datetime.now(UTC)
        items = self.repository.list_trash_items()
        return TrashSummary(
            items=items,
            total_size_bytes=sum(item.entry.size_bytes for item in items),
            expired_count=sum(
                item.entry.state == TrashState.TRASHED and item.entry.purge_after <= current for item in items
            ),
        )

    def purge_expired_trash(self, *, now: datetime | None = None) -> list[TrashActionResult]:
        current = now or datetime.now(UTC)
        expired = [
            item
            for item in self.repository.list_trash_items()
            if item.entry.state == TrashState.TRASHED and item.entry.purge_after <= current
        ]
        return [self.permanently_delete_lesson(item.lesson.lesson_id) for item in expired]

    def recover_trash_operations(self) -> None:
        for entry in self.repository.list_incomplete_trash():
            if entry.state == TrashState.MOVING:
                self._recover_move_to_trash(entry)
            elif entry.state == TrashState.RESTORING:
                self._recover_restore(entry)
            elif entry.state == TrashState.PURGING:
                self._recover_purge(entry)
        for operation in self.repository.list_cleanup_operations():
            if operation.destination_relative_path:
                staging = self.workspace / operation.destination_relative_path
                if staging.exists():
                    shutil.rmtree(staging)
                self._remove_empty_directory(staging.parent)
            self.repository.complete_cleanup(operation.id)

    @staticmethod
    def _remove_empty_directory(path: Path) -> None:
        try:
            path.rmdir()
        except OSError:
            pass

    def _recover_move_to_trash(self, entry: TrashEntry) -> None:
        operation = self.repository.pending_operation(entry.lesson_id, ContentOperationKind.DELETE)
        source = self.workspace / entry.original_relative_path
        destination = self.workspace / entry.trash_relative_path
        if source.exists() and destination.exists():
            raise ContentConflictError(f"Найдены оба каталога операции удаления: {entry.lesson_id}")
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
        self.repository.complete_trash(entry.lesson_id, operation.id)

    def _recover_restore(self, entry: TrashEntry) -> None:
        operation = self.repository.pending_operation(entry.lesson_id, ContentOperationKind.RESTORE)
        source = self.workspace / entry.trash_relative_path
        destination = self.workspace / entry.original_relative_path
        if source.exists() and destination.exists():
            raise ContentConflictError(f"Найдены оба каталога восстановления: {entry.lesson_id}")
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
        self.repository.complete_restore(entry.lesson_id, operation.id)

    def _recover_purge(self, entry: TrashEntry) -> None:
        operation = self.repository.pending_operation(entry.lesson_id, ContentOperationKind.PURGE)
        source = self.workspace / entry.trash_relative_path
        if not entry.staging_relative_path:
            raise ContentConflictError(f"Не указан staging операции очистки: {entry.lesson_id}")
        staging = self.workspace / entry.staging_relative_path
        if source.exists() and staging.exists():
            raise ContentConflictError(f"Найдены оба каталога очистки: {entry.lesson_id}")
        if source.exists():
            staging.parent.mkdir(parents=True, exist_ok=True)
            source.replace(staging)
        self.repository.complete_purge_database(entry.lesson_id, operation.id)
        if staging.exists():
            shutil.rmtree(staging)
        self._remove_empty_directory(staging.parent)
        self.repository.complete_cleanup(operation.id)

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
                "Нельзя одновременно импортировать готовый транскрипт и ставить аудио в очередь"
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
                audio_relative = (
                    Path("lessons")
                    / lesson_id
                    / "recording"
                    / (f"imported_audio{audio_source.suffix.casefold()}")
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
                        media_type=mimetypes.guess_type(audio_target.name)[0] or "application/octet-stream",
                        size_bytes=audio_size,
                        sha256=audio_sha256,
                    )
                )

            if transcript_source:
                transcript_relative = Path("lessons") / lesson_id / "transcript" / "imported_transcript.txt"
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
            self.repository.complete_file_sync(lesson_id)
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
        expected_revision_number: int | None | object = _EXPECTED_REVISION_UNSET,
    ) -> TranscriptRevision:
        lesson = self.repository.get_lesson(lesson_id, include_deleted=True)
        if lesson is None:
            raise ContentNotFoundError(f"Занятие не найдено: {lesson_id}")
        current = self.repository.current_transcript(lesson_id, include_deleted=True)
        if expected_revision_number is _EXPECTED_REVISION_UNSET:
            expected_revision_number = current.revision_number if current else None
        target = (
            path
            or (current.relative_path if current else None)
            or Path("lessons") / lesson_id / "transcript" / "transcript_verified.txt"
        )
        resolved, relative = self._resolve_path(target)
        normalized = text.rstrip() + "\n"
        revision, _updated_lesson = self.repository.commit_transcript_revision(
            TranscriptRevision(
                lesson_id=lesson_id,
                revision_number=1,
                relative_path=relative,
                content=normalized,
                content_sha256=_sha256_text(normalized),
                created_by=created_by,
            ),
            expected_revision_number=expected_revision_number,
            verified_transcript=str(resolved),
        )
        # SQLite is the durable source of truth. Files are refreshed only after the
        # optimistic transaction succeeds, so a conflict can never overwrite them.
        self._synchronize_lesson_files(lesson_id)
        self.repository.delete_transcript_draft(
            lesson_id,
            content_sha256=_sha256_text(text),
            base_revision_number=expected_revision_number,
            conditional=True,
        )
        return revision

    def save_transcript_draft(
        self,
        lesson_id: str,
        text: str,
        *,
        base_revision_number: int | None,
    ) -> TranscriptDraft:
        return self.repository.save_transcript_draft(
            TranscriptDraft(
                lesson_id=lesson_id,
                base_revision_number=base_revision_number,
                content=text,
                content_sha256=_sha256_text(text),
            )
        )

    def discard_transcript_draft(self, lesson_id: str) -> None:
        self.repository.delete_transcript_draft(lesson_id)

    def list_transcript_revisions(self, lesson_id: str) -> list[TranscriptRevision]:
        if not self.repository.get_lesson(lesson_id):
            raise ContentNotFoundError(f"Занятие не найдено: {lesson_id}")
        return self.repository.list_transcript_revisions(lesson_id)

    def compare_transcript_revisions(self, first_id: int, second_id: int) -> str:
        first = self.repository.get_transcript_revision(first_id)
        second = self.repository.get_transcript_revision(second_id)
        if first is None or second is None or first.lesson_id != second.lesson_id:
            raise ContentNotFoundError("Версии транскрипта не найдены в одном занятии")
        return (
            "".join(
                unified_diff(
                    first.content.splitlines(keepends=True),
                    second.content.splitlines(keepends=True),
                    fromfile=f"версия {first.revision_number}",
                    tofile=f"версия {second.revision_number}",
                )
            )
            or "Версии совпадают\n"
        )

    def delete_transcript_revision(self, revision_id: int) -> None:
        self.repository.set_transcript_deleted(revision_id, deleted=True)

    def restore_transcript_revision(self, revision_id: int) -> None:
        self.repository.set_transcript_deleted(revision_id, deleted=False)

    def revert_transcript(
        self,
        revision_id: int,
        *,
        expected_revision_number: int | None | object = _EXPECTED_REVISION_UNSET,
        created_by: str = "teacher-restore",
    ) -> TranscriptRevision:
        revision = self.repository.get_transcript_revision(revision_id, include_deleted=True)
        if revision is None:
            raise ContentNotFoundError(f"Версия транскрипта не найдена: {revision_id}")
        return self.save_transcript(
            revision.lesson_id,
            revision.content,
            path=revision.relative_path,
            created_by=created_by,
            expected_revision_number=expected_revision_number,
        )

    def repair_archive(self) -> IndexReport:
        """Repair managed files and indexes without overwriting SQLite lesson state."""

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
                disk_lesson = Lesson.read_json(lesson_json)
                if disk_lesson.lesson_id != directory.name:
                    raise ValueError(
                        f"lesson_id {disk_lesson.lesson_id!r} не совпадает с каталогом {directory.name!r}"
                    )
                stored = self.repository.get_lesson(directory.name, include_deleted=True)
                if stored is None:
                    self.repository.insert_lesson(disk_lesson)
                    stored = disk_lesson
                content = self.repository.get_content(directory.name, include_deleted=True)
                if content.deleted_at is not None:
                    report.skipped_directories += 1
                    continue
                report.indexed_lessons += 1
                report.indexed_assets += self._synchronize_lesson_files(directory.name)
                report.indexed_transcripts += self._index_lesson_transcript(stored, directory)
            except Exception as exc:
                report.errors.append(f"{directory.name}: {exc}")
        return report

    def index_existing_lessons(self) -> IndexReport:
        """Compatibility alias for the explicit archive repair operation."""

        return self.repair_archive()

    def _index_lesson_assets(self, lesson: Lesson, directory: Path) -> int:
        candidates: set[Path] = {
            candidate
            for candidate in directory.rglob("*")
            if candidate.is_file()
            and not candidate.name.endswith((".tmp", ".part"))
            and candidate.name != "transcript_draft.json"
        }
        candidates.add(directory / "lesson.json")
        transcript_directory = directory / "transcript"
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
            if candidate.suffix.casefold() in AUDIO_IMPORT_SUFFIXES:
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
