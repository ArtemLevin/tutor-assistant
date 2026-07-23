from __future__ import annotations

import hashlib
import logging
import mimetypes
import shutil
from collections.abc import Callable, Collection, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from difflib import unified_diff
from pathlib import Path, PureWindowsPath
from threading import Lock, get_ident
from time import perf_counter
from uuid import uuid4

from ..atomic_io import atomic_write_text
from ..domain import JobStatus, Lesson, Student
from .backup import DatabaseBackupError, DatabaseBackupStore
from .coordination import (
    ActivityLease,
    ActivityLeaseInfo,
    ActivityLeaseStore,
    ContentBusyError,
    process_owner_id,
)
from .importing import (
    DuplicateImportError,
    ImportCancellationToken,
    ImportCancelledError,
    ImportValidationError,
    LessonImportRequest,
    LessonImportResult,
)
from .migrations import apply_migrations
from .models import (
    AssetKind,
    ContentIntegrityIssue,
    ContentIntegrityReport,
    ContentMaintenanceResult,
    ContentOperationKind,
    DatabaseBackupInfo,
    DatabaseBackupRetentionResult,
    DatabaseBackupVerification,
    DatabaseRestoreResult,
    IndexReport,
    IntegrityCheckMode,
    IntegrityScanStats,
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
VOLATILE_CONTENT_STATUSES = {JobStatus.RECORDING, JobStatus.TRANSCRIBING}
REPAIRABLE_CONTENT_ISSUES = {
    "pending_file_sync",
    "failed_file_sync",
    "missing_lesson_directory",
    "invalid_lesson_json",
    "lesson_payload_mismatch",
    "unregistered_asset",
    "asset_changed",
    "missing_transcript",
    "transcript_changed",
}


class ContentPathError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ActivityAcquireResult:
    lease: ActivityLease | None
    blockers: tuple[ActivityLeaseInfo, ...] = ()

    @property
    def acquired(self) -> bool:
        return self.lease is not None


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
        self.backups = DatabaseBackupStore(
            self.repository.path,
            self.workspace / "backups",
        )
        self.lease_store = ActivityLeaseStore(self.workspace / ".operations.sqlite3")
        self.owner_id = process_owner_id()
        self._workspace_generation = self.lease_store.generation()
        self._owned_leases: dict[str, ActivityLease] = {}
        self._owned_leases_lock = Lock()
        self._maintenance_lock = Lock()
        self._maintenance_thread_id: int | None = None
        try:
            with self.activity("startup-recovery", exclusive=True):
                self.recover_trash_operations()
                self.recover_file_sync()
                fts_enabled, fts_documents = self.repository.search_index_status()
                if fts_enabled and fts_documents != len(self.repository.list_lesson_index_states()):
                    self.repository.rebuild_search_index()
        except ContentBusyError:
            logging.info("Startup recovery skipped because another process owns the workspace")

    def try_acquire_activity(
        self,
        activity: str,
        *,
        lesson_id: str | None = None,
        exclusive: bool = False,
        ttl: timedelta = timedelta(minutes=2),
    ) -> ActivityAcquireResult:
        result = self.lease_store.try_acquire(
            owner_id=self.owner_id,
            activity=activity,
            lesson_id=lesson_id,
            exclusive=exclusive,
            ttl=ttl,
        )
        if result.lease_info is None:
            return ActivityAcquireResult(
                lease=None,
                blockers=result.blockers,
            )
        lease = ActivityLease(
            self.lease_store,
            result.lease_info,
            ttl,
            self._forget_owned_lease,
        )
        with self._owned_leases_lock:
            self._owned_leases[lease.info.lease_id] = lease
        return ActivityAcquireResult(lease=lease)

    def _forget_owned_lease(self, lease: ActivityLease) -> None:
        with self._owned_leases_lock:
            self._owned_leases.pop(lease.info.lease_id, None)

    def _current_thread_lease_protects(self, lesson_id: str | None) -> bool:
        thread_id = get_ident()
        with self._owned_leases_lock:
            leases = tuple(self._owned_leases.values())
        return any(
            lease.origin_thread_id == thread_id
            and (
                lease.info.exclusive
                or lesson_id is None
                or lease.info.lesson_id == lesson_id
            )
            for lease in leases
        )

    @contextmanager
    def _write_activity(
        self,
        activity: str,
        *,
        lesson_id: str | None = None,
    ) -> Iterator[None]:
        if self.lease_store.generation() != self._workspace_generation:
            raise ContentBusyError(
                "База данных была восстановлена; перезагрузите данные перед записью"
            )
        if self._current_thread_lease_protects(lesson_id):
            yield
            return
        with self.activity(activity, lesson_id=lesson_id):
            yield

    def acquire_activity(
        self,
        activity: str,
        *,
        lesson_id: str | None = None,
        exclusive: bool = False,
        ttl: timedelta = timedelta(minutes=2),
    ) -> ActivityLease:
        result = self.try_acquire_activity(
            activity,
            lesson_id=lesson_id,
            exclusive=exclusive,
            ttl=ttl,
        )
        if result.lease is None:
            raise ContentBusyError.from_blockers(result.blockers)
        return result.lease

    @contextmanager
    def activity(
        self,
        activity: str,
        *,
        lesson_id: str | None = None,
        exclusive: bool = False,
        ttl: timedelta = timedelta(minutes=2),
    ) -> Iterator[ActivityLease]:
        lease = self.acquire_activity(
            activity,
            lesson_id=lesson_id,
            exclusive=exclusive,
            ttl=ttl,
        )
        try:
            yield lease
        finally:
            lease.release()

    def active_activities(self) -> list[ActivityLeaseInfo]:
        return self.lease_store.active()

    def create_database_backup(self, *, reason: str = "manual") -> DatabaseBackupInfo:
        with self.activity("database-backup", exclusive=True):
            return self.backups.create(reason=reason)

    def list_database_backups(self) -> list[DatabaseBackupInfo]:
        return self.backups.list()

    def verify_database_backup(self, path: Path) -> DatabaseBackupVerification:
        return self.backups.verify(path)

    def prune_database_backups(self, keep: int) -> DatabaseBackupRetentionResult:
        with self.activity("backup-retention", exclusive=True):
            return self.backups.prune(keep)

    def restore_database_backup(self, path: Path) -> DatabaseRestoreResult:
        with self.activity("database-restore", exclusive=True, ttl=timedelta(minutes=5)):
            verification = self.backups.verify(path)
            if not verification.valid:
                raise DatabaseBackupError(
                    "Резервная копия не прошла проверку: " + "; ".join(verification.errors)
                )
            safety = self.backups.create(reason="pre-restore-safety")
            try:
                self.backups.restore_from(path)
                with self.repository.connect() as db:
                    apply_migrations(db)
                self.recover_trash_operations()
                for lesson_id, deleted in self.repository.list_lesson_index_states():
                    if not deleted:
                        self._synchronize_lesson_files(lesson_id)
                self._workspace_generation = self.lease_store.advance_generation()
            except Exception:
                logging.exception("Restore failed; rolling back to the safety backup")
                self.backups.restore_from(safety.path)
                raise
            return DatabaseRestoreResult(
                restored_from=path.resolve(),
                safety_backup=safety,
            )

    @staticmethod
    def restore_database_backup_offline(workspace: Path, path: Path) -> DatabaseRestoreResult:
        workspace = workspace.resolve()
        lease_store = ActivityLeaseStore(workspace / ".operations.sqlite3")
        owner_id = process_owner_id()
        info = lease_store.acquire(
            owner_id=owner_id,
            activity="database-restore-offline",
            exclusive=True,
            ttl=timedelta(minutes=5),
        )
        if info is None:
            raise ContentBusyError("Хранилище занято другим процессом")
        lease = ActivityLease(lease_store, info, timedelta(minutes=5))
        try:
            backups = DatabaseBackupStore(
                workspace / "tutor-assistant.sqlite3",
                workspace / "backups",
            )
            result = backups.restore_offline(path)
            lease_store.advance_generation()
            return result
        finally:
            lease.release()

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
        with self._write_activity("lesson-create", lesson_id=lesson.lesson_id):
            _validate_lesson_id(lesson.lesson_id)
            self.repository.insert_lesson(lesson)
            self._synchronize_lesson_files(lesson.lesson_id)
            return lesson

    def update_lesson(self, lesson: Lesson, *, expected_row_version: int) -> Lesson:
        with self._write_activity("lesson-update", lesson_id=lesson.lesson_id):
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
        with self._write_activity("pipeline-write", lesson_id=lesson.lesson_id):
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
        with self._write_activity("metadata-write", lesson_id=lesson_id):
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

    def inspect_content_integrity(
        self,
        *,
        mode: IntegrityCheckMode = IntegrityCheckMode.FULL,
        lesson_ids: Collection[str] | None = None,
        deadline: datetime | None = None,
        include_storage: bool = True,
    ) -> ContentIntegrityReport:
        started = datetime.now(UTC)
        timer = perf_counter()
        stats = IntegrityScanStats(mode=mode, started_at=started)
        selected = set(lesson_ids) if lesson_ids is not None else None
        database_ok, database_message = self.repository.database_integrity_status()
        fts_enabled, fts_documents = self.repository.search_index_status()
        states = self.repository.list_lesson_index_states()
        indexed_ids = {lesson_id for lesson_id, _deleted in states}
        active_ids = {lesson_id for lesson_id, deleted in states if not deleted}
        deleted_ids = {lesson_id for lesson_id, deleted in states if deleted}
        if selected is not None:
            active_ids &= selected
            deleted_ids &= selected
        active_leases = {
            item.lesson_id: item for item in self.active_activities() if item.lesson_id is not None
        }
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
        if fts_enabled:
            if mode == IntegrityCheckMode.FULL:
                search_messages = {
                    "missing": "Документ занятия отсутствует в FTS",
                    "stale": "FTS содержит устаревшую карточку или транскрипт",
                    "orphan": "FTS содержит документ без занятия в SQLite",
                }
                for lesson_id, state in self.repository.search_index_mismatches():
                    if selected is None or lesson_id in selected:
                        issue(
                            IntegritySeverity.WARNING,
                            f"search_index_{state}",
                            search_messages[state],
                            lesson_id=lesson_id,
                        )
            elif fts_documents != len(states):
                issue(
                    IntegritySeverity.WARNING,
                    "search_index_count",
                    f"FTS содержит {fts_documents} документов вместо {len(states)}",
                )
        else:
            issue(
                IntegritySeverity.INFO,
                "search_fallback",
                "SQLite FTS5 недоступен; используется совместимый линейный поиск",
            )

        for lesson_id, last_error in self.repository.pending_file_sync():
            if selected is None or lesson_id in selected:
                issue(
                    IntegritySeverity.ERROR if last_error else IntegritySeverity.WARNING,
                    "failed_file_sync" if last_error else "pending_file_sync",
                    last_error or "Файловая проекция ожидает восстановления из SQLite",
                    lesson_id=lesson_id,
                    path=f"lessons/{lesson_id}",
                )

        orphan_directories: list[str] = []
        if mode == IntegrityCheckMode.FULL and selected is None:
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
            if deadline is not None and datetime.now(UTC) >= deadline:
                stats.truncated = True
                stats.truncated_reason = "time_budget"
                break
            stats.lessons_examined += 1
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
            disk_lesson: Lesson | None = None
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
            if disk_lesson is not None and disk_lesson != content.lesson:
                issue(
                    IntegritySeverity.WARNING,
                    "lesson_payload_mismatch",
                    "lesson.json отличается от актуальной карточки SQLite",
                    lesson_id=lesson_id,
                    path=lesson_json.relative_to(self.workspace).as_posix(),
                )
            if content.lesson.status in VOLATILE_CONTENT_STATUSES:
                stats.lessons_skipped += 1
                issue(
                    IntegritySeverity.INFO,
                    "active_lesson_skipped",
                    "Проверка изменяемых файлов отложена до завершения pipeline-этапа",
                    lesson_id=lesson_id,
                    path=directory.relative_to(self.workspace).as_posix(),
                )
                continue
            if lesson_id in active_leases:
                stats.lessons_skipped += 1
                issue(
                    IntegritySeverity.INFO,
                    "active_lease_skipped",
                    f"Проверка отложена: выполняется {active_leases[lesson_id].activity}",
                    lesson_id=lesson_id,
                    path=directory.relative_to(self.workspace).as_posix(),
                )
                continue
            registered_paths = {
                asset.relative_path for asset in self.repository.list_assets(lesson_id, include_deleted=True)
            }
            for candidate in self._lesson_asset_candidates(content.lesson, directory):
                try:
                    _absolute, relative = self._resolve_path(candidate)
                except ContentPathError:
                    continue
                if relative not in registered_paths:
                    issue(
                        IntegritySeverity.WARNING,
                        "unregistered_asset",
                        "Файл занятия ещё не зарегистрирован в архиве",
                        lesson_id=lesson_id,
                        path=relative,
                    )
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
                    stat = absolute.stat()
                    stats.assets_stat_checked += 1
                    cache_hit = (
                        mode == IntegrityCheckMode.QUICK
                        and asset.file_mtime_ns == stat.st_mtime_ns
                        and asset.last_verified_at is not None
                        and asset.size_bytes == stat.st_size
                    )
                    if cache_hit:
                        stats.asset_cache_hits += 1
                        continue
                    stats.asset_cache_misses += 1
                    stats.assets_hashed += 1
                    changed = stat.st_size != asset.size_bytes or _sha256_file(absolute) != asset.sha256
                    if not changed and asset.id is not None:
                        self.repository.update_asset_verification(
                            asset.id,
                            file_mtime_ns=stat.st_mtime_ns,
                            verified_at=datetime.now(UTC),
                        )
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
                    if not transcript_path.is_file():
                        issue(
                            IntegritySeverity.WARNING,
                            "missing_transcript",
                            "Подтверждённый транскрипт отсутствует на диске",
                            lesson_id=lesson_id,
                            path=relative,
                        )
                    elif _sha256_file(transcript_path) != content.transcript.content_sha256:
                        issue(
                            IntegritySeverity.WARNING,
                            "transcript_changed",
                            "Файл транскрипта отличается от подтверждённой копии SQLite",
                            lesson_id=lesson_id,
                            path=relative,
                        )
                except (ContentPathError, OSError) as exc:
                    issue(
                        IntegritySeverity.ERROR,
                        "transcript_read",
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
        if selected is None:
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
        stats.completed_at = datetime.now(UTC)
        stats.duration_ms = max(0, int((perf_counter() - timer) * 1000))
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
            storage=self.storage_usage() if include_storage else StorageUsage(),
            issues=issues,
            scan=stats,
        )

    def rebuild_search_index(self) -> int:
        return self.repository.rebuild_search_index()

    def coordinated_rebuild_search_index(self) -> int:
        with self.activity("search-index-rebuild", exclusive=True):
            return self.rebuild_search_index()

    def run_maintenance(
        self,
        *,
        now: datetime | None = None,
        auto_repair: bool = True,
        purge_expired: bool = True,
        cleanup_temporary: bool = True,
        temporary_retention: timedelta = timedelta(hours=24),
        backup_enabled: bool = False,
        backup_interval: timedelta = timedelta(hours=24),
        backup_retention_count: int = 14,
        mode: IntegrityCheckMode = IntegrityCheckMode.QUICK,
        max_lessons: int = 50,
        max_seconds: int = 120,
        apply_max_seconds: int = 30,
    ) -> ContentMaintenanceResult:
        return self._run_maintenance_cycle(
            now=now,
            auto_repair=auto_repair,
            purge_expired=purge_expired,
            cleanup_temporary=cleanup_temporary,
            temporary_retention=temporary_retention,
            backup_enabled=backup_enabled,
            backup_interval=backup_interval,
            backup_retention_count=backup_retention_count,
            mode=mode,
            max_lessons=max_lessons,
            max_seconds=max_seconds,
            apply_max_seconds=apply_max_seconds,
        )

    def run_maintenance_uncoordinated(self, **kwargs) -> ContentMaintenanceResult:
        """Compatibility alias; PR 14 owns its short lease phases internally."""
        return self.run_maintenance(**kwargs)

    def _run_maintenance_cycle(
        self,
        *,
        now: datetime | None,
        auto_repair: bool,
        purge_expired: bool,
        cleanup_temporary: bool,
        temporary_retention: timedelta,
        backup_enabled: bool,
        backup_interval: timedelta,
        backup_retention_count: int,
        mode: IntegrityCheckMode,
        max_lessons: int,
        max_seconds: int,
        apply_max_seconds: int,
    ) -> ContentMaintenanceResult:
        started_at = now or datetime.now(UTC)
        cycle_timer = perf_counter()
        result = ContentMaintenanceResult(started_at=started_at, mode=mode)
        if not self._maintenance_lock.acquire(blocking=False):
            result.skipped = True
            result.skip_reason = "Обслуживание уже выполняется в этом процессе"
            result.completed_at = datetime.now(UTC)
            return result
        try:
            foreign_activities = tuple(
                item for item in self.active_activities() if item.owner_id != self.owner_id
            )
            if foreign_activities:
                result.skipped = True
                result.skip_reason = str(ContentBusyError.from_blockers(foreign_activities))
                return result
            snapshot_timer = perf_counter()
            row_versions = {
                lesson_id: self.repository.lesson_row_version(lesson_id)
                for lesson_id, deleted in self.repository.list_lesson_index_states()
                if not deleted
            }
            result.snapshot_duration_ms = int((perf_counter() - snapshot_timer) * 1000)

            if backup_enabled:
                try:
                    backups = self.backups.list()
                    due = not backups or (started_at - backups[0].manifest.created_at >= backup_interval)
                    if due:
                        with self.activity("database-backup"):
                            result.backup = self.backups.create(reason="scheduled-maintenance")
                    result.backup_retention = self.backups.prune(backup_retention_count)
                    result.errors.extend(
                        f"backup retention: {details}" for details in result.backup_retention.errors
                    )
                except Exception as exc:
                    result.errors.append(f"backup: {exc}")
                    logging.exception("Не удалось создать резервную копию перед обслуживанием")

            deadline = datetime.now(UTC) + timedelta(seconds=max_seconds)
            scan_timer = perf_counter()
            before = self.inspect_content_integrity(
                mode=mode,
                deadline=deadline,
                include_storage=False,
            )
            result.scan_duration_ms = int((perf_counter() - scan_timer) * 1000)
            result.truncated = before.scan.truncated

            targets = sorted(
                {
                    item.lesson_id
                    for item in before.issues
                    if auto_repair and item.lesson_id and item.code in REPAIRABLE_CONTENT_ISSUES
                }
            )
            expired = (
                [
                    item.lesson.lesson_id
                    for item in self.repository.list_trash_items()
                    if item.entry.state == TrashState.TRASHED and item.entry.purge_after <= started_at
                ]
                if purge_expired
                else []
            )
            temporary = (
                self._temporary_candidates(now=started_at, minimum_age=temporary_retention)
                if cleanup_temporary
                else []
            )
            result.planned_repairs = len(targets)
            result.planned_purges = len(expired)
            result.planned_temp_cleanup = len(temporary)

            planned = [("repair", item) for item in targets] + [("purge", item) for item in expired]
            if len(planned) > max_lessons:
                result.truncated = True
                result.deferred_actions += len(planned) - max_lessons
                allowed = planned[:max_lessons]
                targets = [value for kind, value in allowed if kind == "repair"]
                expired = [value for kind, value in allowed if kind == "purge"]

            rebuild_fts = (
                auto_repair
                and before.fts_enabled
                and any(item.code.startswith("search_index_") for item in before.issues)
            )
            if not targets and not expired and not temporary and not rebuild_fts:
                result.report = before
                return result

            apply_timer = perf_counter()
            exclusive_timer = perf_counter()
            with self.activity(
                "content-maintenance-apply",
                exclusive=True,
                ttl=timedelta(minutes=2),
            ):
                self._maintenance_thread_id = get_ident()
                apply_deadline = perf_counter() + apply_max_seconds
                active_lesson_ids = {
                    item.lesson_id
                    for item in self.active_activities()
                    if item.lesson_id is not None and item.activity != "content-maintenance-apply"
                }
                for lesson_id in targets:
                    if perf_counter() >= apply_deadline:
                        result.truncated = True
                        result.deferred_actions += 1
                        continue
                    try:
                        if lesson_id in active_lesson_ids:
                            result.stale_actions.append(f"{lesson_id}: active lease")
                            continue
                        expected = row_versions.get(lesson_id)
                        if expected is None or self.repository.lesson_row_version(lesson_id) != expected:
                            result.stale_actions.append(f"{lesson_id}: row version changed")
                            continue
                        content = self.repository.get_content(lesson_id, include_deleted=True)
                        if content.deleted_at is None and content.lesson.status in VOLATILE_CONTENT_STATUSES:
                            result.stale_actions.append(f"{lesson_id}: active status")
                            continue
                        result.indexed_assets += self._synchronize_lesson_files(lesson_id)
                        result.repaired_lessons.append(lesson_id)
                    except Exception as exc:
                        result.errors.append(f"repair {lesson_id}: {exc}")
                        logging.exception("Не удалось восстановить занятие: %s", lesson_id)
                if rebuild_fts:
                    try:
                        result.rebuilt_search_documents = self.rebuild_search_index()
                    except Exception as exc:
                        result.errors.append(f"search index: {exc}")
                        logging.exception("Не удалось перестроить FTS во время обслуживания")
            result.exclusive_duration_ms = int((perf_counter() - exclusive_timer) * 1000)
            self._maintenance_thread_id = None

            for lesson_id in expired:
                try:
                    self.permanently_delete_lesson(lesson_id)
                    result.purged_lessons.append(lesson_id)
                except Exception as exc:
                    result.errors.append(f"purge {lesson_id}: {exc}")
                    logging.exception("Не удалось автоматически очистить корзину: %s", lesson_id)

            if temporary:
                result.temporary_cleanup = self.cleanup_temporary_files(
                    now=started_at,
                    minimum_age=temporary_retention,
                )
                result.errors.extend(
                    f"temporary cleanup: {details}" for details in result.temporary_cleanup.errors
                )

            result.apply_duration_ms = int((perf_counter() - apply_timer) * 1000)
            result.mutated = bool(
                result.repaired_lessons
                or result.purged_lessons
                or result.temporary_cleanup.removed_paths
                or result.rebuilt_search_documents is not None
            )
            verify_timer = perf_counter()
            result.report = self.inspect_content_integrity(
                mode=IntegrityCheckMode.QUICK,
                lesson_ids=set(result.repaired_lessons),
                include_storage=False,
            )
            result.verify_duration_ms = int((perf_counter() - verify_timer) * 1000)
            return result
        finally:
            result.completed_at = datetime.now(UTC)
            self._maintenance_thread_id = None
            self._maintenance_lock.release()
            logging.info(
                "Обслуживание архива завершено: mode=%s mutated=%s scan_ms=%s "
                "exclusive_ms=%s repaired=%s purged=%s errors=%s total_ms=%s",
                result.mode.value,
                result.mutated,
                result.scan_duration_ms,
                result.exclusive_duration_ms,
                len(result.repaired_lessons),
                len(result.purged_lessons),
                len(result.errors),
                int((perf_counter() - cycle_timer) * 1000),
            )

    def repair_content_integrity(self) -> ContentMaintenanceResult:
        return self.run_maintenance(
            auto_repair=True,
            purge_expired=False,
            cleanup_temporary=False,
            mode=IntegrityCheckMode.FULL,
        )

    def set_trash_retention_days(self, days: int) -> None:
        if not 0 <= days <= 3650:
            raise ValueError("Срок хранения корзины должен быть от 0 до 3650 дней")
        self.repository.reschedule_trash_purge(days)
        self.trash_retention_days = days

    def delete_lesson(self, lesson_id: str) -> TrashActionResult:
        with self.activity("content-delete", lesson_id=lesson_id):
            return self._delete_lesson(lesson_id)

    def _delete_lesson(self, lesson_id: str) -> TrashActionResult:
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
        with self.activity("content-restore", lesson_id=lesson_id):
            return self._restore_lesson(lesson_id)

    def _restore_lesson(self, lesson_id: str) -> TrashActionResult:
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
        if self._maintenance_thread_id == get_ident():
            return self._permanently_delete_lesson(lesson_id)
        with self.activity("content-purge", lesson_id=lesson_id):
            return self._permanently_delete_lesson(lesson_id)

    def stage_lesson_purge(
        self,
        lesson_id: str,
    ) -> tuple[TrashActionResult, str, Path]:
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
        try:
            if source.exists():
                staging.parent.mkdir(parents=True, exist_ok=True)
                source.replace(staging)
                moved = True
            self.repository.complete_purge_database(lesson_id, operation_id)
        except Exception as exc:
            if not moved:
                self.repository.rollback_purge(lesson_id, operation_id, str(exc))
            raise
        return (
            TrashActionResult(
                lesson_id=lesson_id,
                size_bytes=entry.size_bytes,
                operation=ContentOperationKind.PURGE,
            ),
            operation_id,
            staging,
        )

    def finalize_staged_purge(self, operation_id: str, staging: Path) -> None:
        if staging.exists():
            shutil.rmtree(staging)
        self._remove_empty_directory(staging.parent)
        self.repository.complete_cleanup(operation_id)

    def _permanently_delete_lesson(self, lesson_id: str) -> TrashActionResult:
        result, operation_id, staging = self.stage_lesson_purge(lesson_id)
        self.finalize_staged_purge(operation_id, staging)
        return result

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
        with self.activity("content-import", lesson_id=request.lesson_id):
            return self._import_lesson(
                request,
                cancellation=cancellation,
                progress=progress,
            )

    def _import_lesson(
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
        with self._write_activity("asset-write", lesson_id=lesson_id):
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
        with self._write_activity("asset-write"):
            self.repository.set_asset_deleted(asset_id, deleted=True)

    def restore_asset(self, asset_id: int) -> None:
        with self._write_activity("asset-write"):
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
        with self._write_activity("transcript-write", lesson_id=lesson_id):
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
        with self._write_activity("transcript-draft", lesson_id=lesson_id):
            return self.repository.save_transcript_draft(
                TranscriptDraft(
                    lesson_id=lesson_id,
                    base_revision_number=base_revision_number,
                    content=text,
                    content_sha256=_sha256_text(text),
                )
            )

    def discard_transcript_draft(self, lesson_id: str) -> None:
        with self._write_activity("transcript-draft", lesson_id=lesson_id):
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
        with self._write_activity("transcript-write"):
            self.repository.set_transcript_deleted(revision_id, deleted=True)

    def restore_transcript_revision(self, revision_id: int) -> None:
        with self._write_activity("transcript-write"):
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

    def _lesson_asset_candidates(self, lesson: Lesson, directory: Path) -> set[Path]:
        candidates: set[Path] = {
            candidate
            for candidate in directory.rglob("*")
            if candidate.is_file()
            and not candidate.name.endswith((".tmp", ".part"))
            and candidate.name != "transcript_draft.json"
        }
        candidates.add(directory / "lesson.json")
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
        return candidates

    def _index_lesson_assets(self, lesson: Lesson, directory: Path) -> int:
        candidates = self._lesson_asset_candidates(lesson, directory)
        transcript_directory = directory / "transcript"
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
