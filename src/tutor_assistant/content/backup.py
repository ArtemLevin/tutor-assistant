from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ..atomic_io import atomic_write_text
from ..sqlite_utils import ClosingConnection
from .models import (
    DatabaseBackupInfo,
    DatabaseBackupManifest,
    DatabaseBackupRetentionResult,
    DatabaseBackupVerification,
    DatabaseRestoreResult,
)


class DatabaseBackupError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class DatabaseBackupStore:
    def __init__(self, database_path: Path, backup_directory: Path) -> None:
        self.database_path = database_path.resolve()
        self.directory = backup_directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _manifest_path(database_file: Path) -> Path:
        return database_file.with_suffix(database_file.suffix + ".manifest.json")

    @staticmethod
    def _sqlite_check(path: Path) -> list[str]:
        errors: list[str] = []
        try:
            uri = f"{path.resolve().as_uri()}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=10, factory=ClosingConnection) as db:
                quick_check = db.execute("PRAGMA quick_check").fetchall()
                messages = [str(row[0]) for row in quick_check]
                if messages != ["ok"]:
                    errors.extend(f"SQLite quick_check: {message}" for message in messages)
                foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
                if foreign_keys:
                    errors.append(f"SQLite foreign_key_check: {len(foreign_keys)} нарушений")
        except (OSError, sqlite3.DatabaseError) as exc:
            errors.append(f"SQLite не читается: {exc}")
        return errors

    @staticmethod
    def _schema_version(path: Path) -> int:
        with sqlite3.connect(path, timeout=10, factory=ClosingConnection) as db:
            try:
                row = db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
            except sqlite3.OperationalError:
                return 0
        return int(row[0]) if row else 0

    def create(self, *, reason: str = "manual") -> DatabaseBackupInfo:
        now = datetime.now(UTC)
        backup_id = uuid4().hex
        filename = f"tutor-assistant-{now:%Y%m%dT%H%M%S%fZ}-{backup_id[:8]}.sqlite3"
        final_path = self.directory / filename
        temporary_path = self.directory / f".{filename}.tmp"
        try:
            with sqlite3.connect(self.database_path, timeout=10, factory=ClosingConnection) as source:
                with sqlite3.connect(temporary_path, timeout=10, factory=ClosingConnection) as destination:
                    source.backup(destination)
            errors = self._sqlite_check(temporary_path)
            if errors:
                raise DatabaseBackupError("; ".join(errors))
            size_bytes = temporary_path.stat().st_size
            sha256 = _sha256_file(temporary_path)
            manifest = DatabaseBackupManifest(
                backup_id=backup_id,
                created_at=now,
                reason=reason,
                database_file=filename,
                source_database_name=self.database_path.name,
                size_bytes=size_bytes,
                sha256=sha256,
                schema_version=self._schema_version(temporary_path),
            )
            temporary_path.replace(final_path)
            manifest_path = self._manifest_path(final_path)
            atomic_write_text(manifest_path, manifest.model_dump_json(indent=2))
            return DatabaseBackupInfo(
                path=final_path,
                manifest_path=manifest_path,
                manifest=manifest,
            )
        except Exception:
            temporary_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            self._manifest_path(final_path).unlink(missing_ok=True)
            raise

    def verify(self, path: Path) -> DatabaseBackupVerification:
        resolved = path.resolve()
        errors: list[str] = []
        manifest: DatabaseBackupManifest | None = None
        if not resolved.is_file():
            return DatabaseBackupVerification(
                path=resolved,
                valid=False,
                errors=["Файл резервной копии не найден"],
            )
        manifest_path = self._manifest_path(resolved)
        try:
            manifest = DatabaseBackupManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"Manifest отсутствует или повреждён: {exc}")
        if manifest:
            if manifest.database_file != resolved.name:
                errors.append("Manifest относится к другому файлу")
            actual_size = resolved.stat().st_size
            if manifest.size_bytes != actual_size:
                errors.append(f"Размер не совпадает: ожидалось {manifest.size_bytes}, получено {actual_size}")
            actual_sha256 = _sha256_file(resolved)
            if manifest.sha256 != actual_sha256:
                errors.append("Контрольная сумма SHA-256 не совпадает")
        errors.extend(self._sqlite_check(resolved))
        return DatabaseBackupVerification(
            path=resolved,
            valid=not errors,
            errors=errors,
            manifest=manifest,
        )

    def list(self) -> list[DatabaseBackupInfo]:
        backups: list[DatabaseBackupInfo] = []
        for manifest_path in self.directory.glob("*.sqlite3.manifest.json"):
            try:
                manifest = DatabaseBackupManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
                database_path = self.directory / manifest.database_file
                if database_path.is_file() and database_path.parent == self.directory:
                    backups.append(
                        DatabaseBackupInfo(
                            path=database_path,
                            manifest_path=manifest_path,
                            manifest=manifest,
                        )
                    )
            except Exception:
                continue
        return sorted(backups, key=lambda item: item.manifest.created_at, reverse=True)

    def prune(self, keep: int) -> DatabaseBackupRetentionResult:
        if keep < 1:
            raise ValueError("Должна сохраняться хотя бы одна резервная копия")
        result = DatabaseBackupRetentionResult()
        for backup in self.list()[keep:]:
            try:
                backup.path.unlink()
                backup.manifest_path.unlink(missing_ok=True)
                result.removed.append(backup.path)
            except OSError as exc:
                result.errors.append(f"{backup.path.name}: {exc}")
        return result

    def restore_from(self, path: Path) -> None:
        verification = self.verify(path)
        if not verification.valid:
            raise DatabaseBackupError("Резервная копия не прошла проверку: " + "; ".join(verification.errors))
        try:
            with sqlite3.connect(path.resolve(), timeout=10, factory=ClosingConnection) as source:
                with sqlite3.connect(
                    self.database_path, timeout=10, factory=ClosingConnection
                ) as destination:
                    source.backup(destination)
                    destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except (OSError, sqlite3.DatabaseError) as exc:
            raise DatabaseBackupError(f"Не удалось восстановить SQLite: {exc}") from exc
        errors = self._sqlite_check(self.database_path)
        if errors:
            raise DatabaseBackupError("Восстановленная SQLite повреждена: " + "; ".join(errors))

    def restore_offline(self, path: Path) -> DatabaseRestoreResult:
        """Replace a closed/corrupted live DB while preserving every old sidecar."""

        verification = self.verify(path)
        if not verification.valid:
            raise DatabaseBackupError("Резервная копия не прошла проверку: " + "; ".join(verification.errors))
        recovery_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
        safety_directory = self.directory / f"pre-restore-raw-{recovery_id}"
        temporary_path = self.database_path.with_suffix(".restore.tmp")
        moved: list[tuple[Path, Path]] = []
        try:
            temporary_path.unlink(missing_ok=True)
            with sqlite3.connect(path.resolve(), timeout=10, factory=ClosingConnection) as source:
                with sqlite3.connect(temporary_path, timeout=10, factory=ClosingConnection) as destination:
                    source.backup(destination)
            errors = self._sqlite_check(temporary_path)
            if errors:
                raise DatabaseBackupError("Подготовленный restore повреждён: " + "; ".join(errors))
            safety_directory.mkdir(parents=True, exist_ok=False)
            for live_path in (
                self.database_path,
                Path(str(self.database_path) + "-wal"),
                Path(str(self.database_path) + "-shm"),
            ):
                if live_path.exists():
                    safety_path = safety_directory / live_path.name
                    os.replace(live_path, safety_path)
                    moved.append((live_path, safety_path))
            os.replace(temporary_path, self.database_path)
            errors = self._sqlite_check(self.database_path)
            if errors:
                raise DatabaseBackupError("Восстановленная SQLite повреждена: " + "; ".join(errors))
            if not moved:
                safety_directory.rmdir()
            return DatabaseRestoreResult(
                restored_from=path.resolve(),
                raw_safety_path=safety_directory if moved else None,
            )
        except Exception:
            temporary_path.unlink(missing_ok=True)
            if moved:
                failed_path = safety_directory / "failed-restored.sqlite3"
                if self.database_path.exists():
                    os.replace(self.database_path, failed_path)
                for live_path, safety_path in moved:
                    if safety_path.exists():
                        os.replace(safety_path, live_path)
            raise
