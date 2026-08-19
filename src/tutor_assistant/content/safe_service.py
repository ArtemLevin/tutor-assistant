from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

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
    """

    def _restore_database_file(self, path: Path) -> None:
        self.backups.restore_from(path)
        with self.repository.connect() as db:
            apply_migrations(db)

    def _reproject_workspace_from_database(self) -> None:
        self.recover_trash_operations()
        for lesson_id, _deleted in self.repository.list_lesson_index_states():
            self._synchronize_lesson_files(lesson_id)

    def restore_database_backup(self, path: Path) -> DatabaseRestoreResult:
        with self.activity("database-restore", exclusive=True, ttl=timedelta(minutes=5)):
            verification = self.backups.verify(path)
            if not verification.valid:
                raise DatabaseBackupError(
                    "Резервная копия не прошла проверку: " + "; ".join(verification.errors)
                )
            safety = self.backups.create(reason="pre-restore-safety")
            try:
                self._restore_database_file(path)
                self._reproject_workspace_from_database()
            except Exception as restore_error:
                logging.exception(
                    "Restore failed; restoring both database and managed filesystem projection"
                )
                try:
                    self._restore_database_file(safety.path)
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
            self._workspace_generation = self.lease_store.advance_generation()
            return DatabaseRestoreResult(
                restored_from=path.resolve(),
                safety_backup=safety,
            )
