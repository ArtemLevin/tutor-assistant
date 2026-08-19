from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ..atomic_io import atomic_write_text
from .backup import DatabaseBackupError
from .migrations import apply_migrations
from .models import DatabaseRestoreResult
from .service import StudentContentService as BaseStudentContentService


class StudentContentService(BaseStudentContentService):
    """Safety-hardened content boundary used by production imports.

    SQLite remains authoritative for managed lesson metadata and transcript
    projections. Database restore therefore commits only after both the restored
    database and its managed filesystem projection are consistent. If projection
    fails, the pre-restore safety database is restored and projected back to disk
    before the original exception is re-raised.

    Lesson directories that do not exist in the restored database are never
    deleted. They are moved outside the active ``lessons/`` namespace into a
    recovery quarantine so archive repair cannot silently resurrect post-backup
    lessons. A failed restore moves them back before the safety projection runs.
    """

    def _restore_database_file(self, path: Path) -> None:
        self.backups.restore_from(path)
        with self.repository.connect() as db:
            apply_migrations(db)

    def _reproject_workspace_from_database(self) -> None:
        self.recover_trash_operations()
        for lesson_id, _deleted in self.repository.list_lesson_index_states():
            self._synchronize_lesson_files(lesson_id)

    def _active_database_lesson_ids(self) -> set[str]:
        return {
            lesson_id
            for lesson_id, deleted in self.repository.list_lesson_index_states()
            if not deleted
        }

    def _quarantine_orphan_lesson_directories(
        self,
        valid_lesson_ids: set[str],
    ) -> tuple[Path | None, list[tuple[Path, Path]]]:
        lessons_root = self.workspace / "lessons"
        if not lessons_root.is_dir():
            return None, []
        candidates = [
            path
            for path in sorted(lessons_root.iterdir())
            if path.is_dir() and not path.is_symlink() and path.name not in valid_lesson_ids
        ]
        if not candidates:
            return None, []

        quarantine_root = self.workspace / ".restore-quarantine" / uuid4().hex
        quarantine_lessons = quarantine_root / "lessons"
        quarantine_lessons.mkdir(parents=True, exist_ok=False)
        moved: list[tuple[Path, Path]] = []
        try:
            for source in candidates:
                target = quarantine_lessons / source.name
                source.replace(target)
                moved.append((source, target))
            manifest = {
                "created_at": datetime.now(UTC).isoformat(),
                "reason": "database-restore-orphans",
                "lessons": [
                    {
                        "lesson_id": source.name,
                        "original_path": str(source.resolve()),
                        "quarantined_path": str(target.resolve()),
                    }
                    for source, target in moved
                ],
            }
            atomic_write_text(
                quarantine_root / "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
        except Exception:
            self._restore_quarantined_lesson_directories(moved)
            raise
        return quarantine_root, moved

    @staticmethod
    def _restore_quarantined_lesson_directories(
        moved: list[tuple[Path, Path]],
    ) -> None:
        for original, quarantined in reversed(moved):
            if not quarantined.exists():
                continue
            if original.exists():
                raise DatabaseBackupError(
                    "Safety rollback не может вернуть каталог занятия: "
                    f"целевой путь уже существует: {original}"
                )
            original.parent.mkdir(parents=True, exist_ok=True)
            quarantined.replace(original)

    @staticmethod
    def _remove_empty_quarantine(quarantine_root: Path | None) -> None:
        if quarantine_root is None or not quarantine_root.exists():
            return
        manifest = quarantine_root / "manifest.json"
        manifest.unlink(missing_ok=True)
        lessons = quarantine_root / "lessons"
        try:
            lessons.rmdir()
            quarantine_root.rmdir()
            quarantine_root.parent.rmdir()
        except OSError:
            pass

    def restore_database_backup(self, path: Path) -> DatabaseRestoreResult:
        with self.activity("database-restore", exclusive=True, ttl=timedelta(minutes=5)):
            verification = self.backups.verify(path)
            if not verification.valid:
                raise DatabaseBackupError(
                    "Резервная копия не прошла проверку: " + "; ".join(verification.errors)
                )
            safety = self.backups.create(reason="pre-restore-safety")
            quarantine_root: Path | None = None
            quarantined: list[tuple[Path, Path]] = []
            try:
                self._restore_database_file(path)
                quarantine_root, quarantined = self._quarantine_orphan_lesson_directories(
                    self._active_database_lesson_ids()
                )
                self._reproject_workspace_from_database()
            except Exception as restore_error:
                logging.exception(
                    "Restore failed; restoring both database and managed filesystem projection"
                )
                try:
                    self._restore_database_file(safety.path)
                    self._restore_quarantined_lesson_directories(quarantined)
                    self._remove_empty_quarantine(quarantine_root)
                    self._reproject_workspace_from_database()
                except Exception as rollback_error:
                    logging.exception("Safety rollback failed after database restore failure")
                    combined = ExceptionGroup(
                        "Database restore and safety rollback both failed",
                        [restore_error, rollback_error],
                    )
                    raise DatabaseBackupError(
                        "Восстановление не завершено, а safety rollback также завершился ошибкой. "
                        "Рабочее пространство требует ручной проверки."
                    ) from combined
                raise
            if quarantine_root is not None:
                logging.warning(
                    "Post-backup lesson directories moved to restore quarantine: %s",
                    quarantine_root,
                )
            self._workspace_generation = self.lease_store.advance_generation()
            return DatabaseRestoreResult(
                restored_from=path.resolve(),
                safety_backup=safety,
            )
