from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one occurrence, found {count}")
    return text.replace(old, new, 1)


def replace_regex(text: str, pattern: str, replacement: str, *, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    return updated


# ---------------------------------------------------------------------------
# Configuration budgets
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/config.py"
text = read(path)
text = replace_once(
    text,
    "    backup_retention_count: int = Field(default=14, ge=1, le=365)\n",
    "    backup_retention_count: int = Field(default=14, ge=1, le=365)\n"
    "    maintenance_max_lessons_per_cycle: int = Field(default=50, ge=1, le=10_000)\n"
    "    maintenance_max_seconds: int = Field(default=120, ge=10, le=3600)\n"
    "    maintenance_apply_max_seconds: int = Field(default=30, ge=5, le=600)\n"
    "    maintenance_full_scan_interval_hours: int = Field(default=168, ge=1, le=8760)\n",
    label="content config budgets",
)
write(path, text)


# ---------------------------------------------------------------------------
# Content models
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/content/models.py"
text = read(path)
text = replace_once(
    text,
    "class IntegritySeverity(StrEnum):\n    ERROR = \"error\"\n    WARNING = \"warning\"\n    INFO = \"info\"\n\n\n",
    "class IntegritySeverity(StrEnum):\n    ERROR = \"error\"\n    WARNING = \"warning\"\n    INFO = \"info\"\n\n\n"
    "class IntegrityCheckMode(StrEnum):\n"
    "    QUICK = \"quick\"\n"
    "    FULL = \"full\"\n\n\n",
    label="integrity check mode",
)
text = replace_once(
    text,
    "    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))\n    deleted_at: datetime | None = None\n",
    "    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))\n"
    "    deleted_at: datetime | None = None\n"
    "    file_mtime_ns: int | None = Field(default=None, ge=0)\n"
    "    last_verified_at: datetime | None = None\n",
    label="asset cache fields",
)
text = replace_once(
    text,
    "class ContentIntegrityReport(BaseModel):\n",
    "class IntegrityScanStats(BaseModel):\n"
    "    mode: IntegrityCheckMode = IntegrityCheckMode.FULL\n"
    "    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))\n"
    "    completed_at: datetime | None = None\n"
    "    lessons_examined: int = Field(default=0, ge=0)\n"
    "    lessons_skipped: int = Field(default=0, ge=0)\n"
    "    assets_stat_checked: int = Field(default=0, ge=0)\n"
    "    assets_hashed: int = Field(default=0, ge=0)\n"
    "    asset_cache_hits: int = Field(default=0, ge=0)\n"
    "    asset_cache_misses: int = Field(default=0, ge=0)\n"
    "    truncated: bool = False\n"
    "    truncated_reason: str | None = None\n"
    "    duration_ms: int = Field(default=0, ge=0)\n\n\n"
    "class ContentIntegrityReport(BaseModel):\n",
    label="scan stats model",
)
text = replace_once(
    text,
    "    issues: list[ContentIntegrityIssue] = Field(default_factory=list)\n",
    "    issues: list[ContentIntegrityIssue] = Field(default_factory=list)\n"
    "    scan: IntegrityScanStats = Field(default_factory=IntegrityScanStats)\n",
    label="report scan field",
)
text = replace_once(
    text,
    "    report: ContentIntegrityReport | None = None\n",
    "    report: ContentIntegrityReport | None = None\n"
    "    mode: IntegrityCheckMode = IntegrityCheckMode.QUICK\n"
    "    mutated: bool = False\n"
    "    truncated: bool = False\n"
    "    deferred_actions: int = Field(default=0, ge=0)\n"
    "    snapshot_duration_ms: int = Field(default=0, ge=0)\n"
    "    scan_duration_ms: int = Field(default=0, ge=0)\n"
    "    apply_duration_ms: int = Field(default=0, ge=0)\n"
    "    verify_duration_ms: int = Field(default=0, ge=0)\n"
    "    exclusive_duration_ms: int = Field(default=0, ge=0)\n"
    "    planned_repairs: int = Field(default=0, ge=0)\n"
    "    planned_purges: int = Field(default=0, ge=0)\n"
    "    planned_temp_cleanup: int = Field(default=0, ge=0)\n"
    "    stale_actions: list[str] = Field(default_factory=list)\n",
    label="maintenance metrics",
)
write(path, text)


# ---------------------------------------------------------------------------
# Migration 7
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/content/migrations.py"
text = read(path)
insert = '''\n\ndef _asset_verification_cache(db: sqlite3.Connection) -> None:\n    _add_column(db, "lesson_assets", "file_mtime_ns INTEGER")\n    _add_column(db, "lesson_assets", "last_verified_at TEXT")\n    db.execute(\n        "CREATE INDEX IF NOT EXISTS lesson_assets_verification "\n        "ON lesson_assets(last_verified_at, deleted_at)"\n    )\n'''
text = replace_once(
    text,
    "\n\nMIGRATIONS = (\n",
    insert + "\n\nMIGRATIONS = (\n",
    label="migration function",
)
text = replace_once(
    text,
    "    Migration(6, \"content_write_consistency\", _content_write_consistency),\n",
    "    Migration(6, \"content_write_consistency\", _content_write_consistency),\n"
    "    Migration(7, \"asset_verification_cache\", _asset_verification_cache),\n",
    label="migration registration",
)
write(path, text)


# ---------------------------------------------------------------------------
# Repository cache persistence
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/content/repository.py"
text = read(path)
text = text.replace(
    "a.size_bytes, a.sha256, a.created_at, a.updated_at, a.deleted_at ",
    "a.size_bytes, a.sha256, a.created_at, a.updated_at, a.deleted_at, "
    "a.file_mtime_ns, a.last_verified_at ",
)
text = text.replace(
    "size_bytes, sha256, created_at, updated_at, deleted_at\n",
    "size_bytes, sha256, created_at, updated_at, deleted_at, "
    "file_mtime_ns, last_verified_at\n",
)
# Import bundle asset INSERT.
text = replace_once(
    text,
    "                            sha256, created_at, updated_at, deleted_at\n                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)\n",
    "                            sha256, created_at, updated_at, deleted_at,\n"
    "                            file_mtime_ns, last_verified_at\n"
    "                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n",
    label="import asset insert columns",
)
text = replace_once(
    text,
    "                            asset.deleted_at.isoformat() if asset.deleted_at else None,\n                        ),\n",
    "                            asset.deleted_at.isoformat() if asset.deleted_at else None,\n"
    "                            asset.file_mtime_ns,\n"
    "                            asset.last_verified_at.isoformat() if asset.last_verified_at else None,\n"
    "                        ),\n",
    label="import asset insert values",
)
# Upsert asset INSERT and cache update.
text = replace_once(
    text,
    "                        created_at, updated_at, deleted_at\n                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)\n",
    "                        created_at, updated_at, deleted_at, file_mtime_ns, last_verified_at\n"
    "                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n",
    label="upsert asset columns",
)
text = replace_once(
    text,
    "                        updated_at=excluded.updated_at,\n                        deleted_at=CASE\n",
    "                        updated_at=excluded.updated_at,\n"
    "                        file_mtime_ns=excluded.file_mtime_ns,\n"
    "                        last_verified_at=excluded.last_verified_at,\n"
    "                        deleted_at=CASE\n",
    label="upsert cache update",
)
text = replace_once(
    text,
    "                        asset.deleted_at.isoformat() if asset.deleted_at else None,\n                    ),\n",
    "                        asset.deleted_at.isoformat() if asset.deleted_at else None,\n"
    "                        asset.file_mtime_ns,\n"
    "                        asset.last_verified_at.isoformat() if asset.last_verified_at else None,\n"
    "                    ),\n",
    label="upsert cache values",
)
# Ensure direct SELECT after upsert returns cache fields.
text = text.replace(
    "sha256, created_at, updated_at, deleted_at\n                    FROM lesson_assets",
    "sha256, created_at, updated_at, deleted_at, file_mtime_ns, last_verified_at\n"
    "                    FROM lesson_assets",
)
# Add update method before list_assets.
marker = "    def list_assets(self, lesson_id: str, *, include_deleted: bool = False) -> list[LessonAsset]:\n"
method = '''    def update_asset_verification(\n        self,\n        asset_id: int,\n        *,\n        file_mtime_ns: int,\n        verified_at: datetime,\n    ) -> None:\n        def operation() -> None:\n            with self.connect() as db:\n                cursor = db.execute(\n                    "UPDATE lesson_assets SET file_mtime_ns=?, last_verified_at=? WHERE id=?",\n                    (file_mtime_ns, verified_at.isoformat(), asset_id),\n                )\n                if cursor.rowcount == 0:\n                    raise ContentNotFoundError(f"Файл занятия не найден: {asset_id}")\n\n        self._retry(operation)\n\n'''
text = replace_once(text, marker, method + marker, label="verification update method")
write(path, text)


# ---------------------------------------------------------------------------
# Service: incremental integrity and phased maintenance
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/content/service.py"
text = read(path)
text = text.replace("from collections.abc import Callable, Iterator", "from collections.abc import Callable, Collection, Iterator")
text = text.replace("from threading import Lock, get_ident", "from threading import Lock, get_ident")
text = text.replace("from uuid import uuid4", "from time import perf_counter\nfrom uuid import uuid4")
text = replace_once(
    text,
    "    IndexReport,\n    IntegritySeverity,\n",
    "    IndexReport,\n    IntegrityCheckMode,\n    IntegrityScanStats,\n    IntegritySeverity,\n",
    label="service model imports",
)
new_integrity = '''    def inspect_content_integrity(\n        self,\n        *,\n        mode: IntegrityCheckMode = IntegrityCheckMode.FULL,\n        lesson_ids: Collection[str] | None = None,\n        deadline: datetime | None = None,\n        include_storage: bool = True,\n    ) -> ContentIntegrityReport:\n        started = datetime.now(UTC)\n        timer = perf_counter()\n        stats = IntegrityScanStats(mode=mode, started_at=started)\n        selected = set(lesson_ids) if lesson_ids is not None else None\n        database_ok, database_message = self.repository.database_integrity_status()\n        fts_enabled, fts_documents = self.repository.search_index_status()\n        states = self.repository.list_lesson_index_states()\n        indexed_ids = {lesson_id for lesson_id, _deleted in states}\n        active_ids = {lesson_id for lesson_id, deleted in states if not deleted}\n        deleted_ids = {lesson_id for lesson_id, deleted in states if deleted}\n        if selected is not None:\n            active_ids &= selected\n            deleted_ids &= selected\n        active_leases = {\n            item.lesson_id: item\n            for item in self.active_activities()\n            if item.lesson_id is not None\n        }\n        trash_entries = {item.lesson.lesson_id: item.entry for item in self.repository.list_trash_items()}\n        lesson_root = self.workspace / "lessons"\n        trash_root = self.workspace / "trash" / "lessons"\n        lesson_directories = (\n            {path.name: path for path in lesson_root.iterdir() if path.is_dir()}\n            if lesson_root.is_dir()\n            else {}\n        )\n        trash_directories = (\n            {path.name: path for path in trash_root.iterdir() if path.is_dir()}\n            if trash_root.is_dir()\n            else {}\n        )\n        issues: list[ContentIntegrityIssue] = []\n\n        def issue(\n            severity: IntegritySeverity,\n            code: str,\n            message: str,\n            *,\n            lesson_id: str | None = None,\n            path: str | None = None,\n        ) -> None:\n            issues.append(\n                ContentIntegrityIssue(\n                    severity=severity,\n                    code=code,\n                    message=message,\n                    lesson_id=lesson_id,\n                    relative_path=path,\n                )\n            )\n\n        if not database_ok:\n            issue(IntegritySeverity.ERROR, "database", database_message)\n        if fts_enabled:\n            if mode == IntegrityCheckMode.FULL:\n                search_messages = {\n                    "missing": "Документ занятия отсутствует в FTS",\n                    "stale": "FTS содержит устаревшую карточку или транскрипт",\n                    "orphan": "FTS содержит документ без занятия в SQLite",\n                }\n                for lesson_id, state in self.repository.search_index_mismatches():\n                    if selected is None or lesson_id in selected:\n                        issue(\n                            IntegritySeverity.WARNING,\n                            f"search_index_{state}",\n                            search_messages[state],\n                            lesson_id=lesson_id,\n                        )\n            elif fts_documents != len(states):\n                issue(\n                    IntegritySeverity.WARNING,\n                    "search_index_count",\n                    f"FTS содержит {fts_documents} документов вместо {len(states)}",\n                )\n        else:\n            issue(\n                IntegritySeverity.INFO,\n                "search_fallback",\n                "SQLite FTS5 недоступен; используется совместимый линейный поиск",\n            )\n\n        for lesson_id, last_error in self.repository.pending_file_sync():\n            if selected is None or lesson_id in selected:\n                issue(\n                    IntegritySeverity.ERROR if last_error else IntegritySeverity.WARNING,\n                    "failed_file_sync" if last_error else "pending_file_sync",\n                    last_error or "Файловая проекция ожидает восстановления из SQLite",\n                    lesson_id=lesson_id,\n                    path=f"lessons/{lesson_id}",\n                )\n\n        orphan_directories: list[str] = []\n        if mode == IntegrityCheckMode.FULL and selected is None:\n            orphan_directories = [\n                path.relative_to(self.workspace).as_posix()\n                for lesson_id, path in lesson_directories.items()\n                if lesson_id not in indexed_ids\n            ]\n            orphan_directories.extend(\n                path.relative_to(self.workspace).as_posix()\n                for lesson_id, path in trash_directories.items()\n                if lesson_id not in trash_entries\n            )\n            for relative in orphan_directories:\n                issue(\n                    IntegritySeverity.WARNING,\n                    "orphan_directory",\n                    "Каталог не связан с записью SQLite и оставлен без изменений",\n                    path=relative,\n                )\n\n        for lesson_id in sorted(active_ids):\n            if deadline is not None and datetime.now(UTC) >= deadline:\n                stats.truncated = True\n                stats.truncated_reason = "time_budget"\n                break\n            stats.lessons_examined += 1\n            directory = lesson_directories.get(lesson_id)\n            if directory is None:\n                issue(\n                    IntegritySeverity.ERROR,\n                    "missing_lesson_directory",\n                    "Для активного занятия отсутствует управляемый каталог",\n                    lesson_id=lesson_id,\n                    path=f"lessons/{lesson_id}",\n                )\n                continue\n            lesson_json = directory / "lesson.json"\n            disk_lesson: Lesson | None = None\n            try:\n                disk_lesson = Lesson.read_json(lesson_json)\n                if disk_lesson.lesson_id != lesson_id:\n                    raise ValueError("lesson_id не совпадает с именем каталога")\n            except Exception as exc:\n                issue(\n                    IntegritySeverity.ERROR,\n                    "invalid_lesson_json",\n                    str(exc),\n                    lesson_id=lesson_id,\n                    path=lesson_json.relative_to(self.workspace).as_posix(),\n                )\n            try:\n                content = self.repository.get_content(lesson_id)\n            except Exception as exc:\n                issue(IntegritySeverity.ERROR, "content_read", str(exc), lesson_id=lesson_id)\n                continue\n            if disk_lesson is not None and disk_lesson != content.lesson:\n                issue(\n                    IntegritySeverity.WARNING,\n                    "lesson_payload_mismatch",\n                    "lesson.json отличается от актуальной карточки SQLite",\n                    lesson_id=lesson_id,\n                    path=lesson_json.relative_to(self.workspace).as_posix(),\n                )\n            if content.lesson.status in VOLATILE_CONTENT_STATUSES:\n                stats.lessons_skipped += 1\n                issue(\n                    IntegritySeverity.INFO,\n                    "active_lesson_skipped",\n                    "Проверка изменяемых файлов отложена до завершения pipeline-этапа",\n                    lesson_id=lesson_id,\n                    path=directory.relative_to(self.workspace).as_posix(),\n                )\n                continue\n            if lesson_id in active_leases:\n                stats.lessons_skipped += 1\n                issue(\n                    IntegritySeverity.INFO,\n                    "active_lease_skipped",\n                    f"Проверка отложена: выполняется {active_leases[lesson_id].activity}",\n                    lesson_id=lesson_id,\n                    path=directory.relative_to(self.workspace).as_posix(),\n                )\n                continue\n            registered_paths = {\n                asset.relative_path for asset in self.repository.list_assets(lesson_id, include_deleted=True)\n            }\n            for candidate in self._lesson_asset_candidates(content.lesson, directory):\n                try:\n                    _absolute, relative = self._resolve_path(candidate)\n                except ContentPathError:\n                    continue\n                if relative not in registered_paths:\n                    issue(\n                        IntegritySeverity.WARNING,\n                        "unregistered_asset",\n                        "Файл занятия ещё не зарегистрирован в архиве",\n                        lesson_id=lesson_id,\n                        path=relative,\n                    )\n            for asset in content.assets:\n                try:\n                    absolute, relative = self._resolve_path(asset.relative_path)\n                except ContentPathError as exc:\n                    issue(\n                        IntegritySeverity.ERROR,\n                        "unsafe_path",\n                        str(exc),\n                        lesson_id=lesson_id,\n                        path=asset.relative_path,\n                    )\n                    continue\n                if not absolute.is_file():\n                    issue(\n                        IntegritySeverity.WARNING,\n                        "missing_asset",\n                        "Зарегистрированный файл отсутствует",\n                        lesson_id=lesson_id,\n                        path=relative,\n                    )\n                    continue\n                try:\n                    stat = absolute.stat()\n                    stats.assets_stat_checked += 1\n                    cache_hit = (\n                        mode == IntegrityCheckMode.QUICK\n                        and asset.file_mtime_ns == stat.st_mtime_ns\n                        and asset.last_verified_at is not None\n                        and asset.size_bytes == stat.st_size\n                    )\n                    if cache_hit:\n                        stats.asset_cache_hits += 1\n                        continue\n                    stats.asset_cache_misses += 1\n                    stats.assets_hashed += 1\n                    changed = stat.st_size != asset.size_bytes or _sha256_file(absolute) != asset.sha256\n                    if not changed and asset.id is not None:\n                        self.repository.update_asset_verification(\n                            asset.id,\n                            file_mtime_ns=stat.st_mtime_ns,\n                            verified_at=datetime.now(UTC),\n                        )\n                except OSError as exc:\n                    issue(\n                        IntegritySeverity.ERROR,\n                        "asset_read",\n                        str(exc),\n                        lesson_id=lesson_id,\n                        path=relative,\n                    )\n                    continue\n                if changed:\n                    issue(\n                        IntegritySeverity.WARNING,\n                        "asset_changed",\n                        "Размер или SHA-256 файла отличается от индекса",\n                        lesson_id=lesson_id,\n                        path=relative,\n                    )\n            if content.transcript:\n                try:\n                    transcript_path, relative = self._resolve_path(content.transcript.relative_path)\n                    if not transcript_path.is_file():\n                        issue(\n                            IntegritySeverity.WARNING,\n                            "missing_transcript",\n                            "Подтверждённый транскрипт отсутствует на диске",\n                            lesson_id=lesson_id,\n                            path=relative,\n                        )\n                    elif _sha256_file(transcript_path) != content.transcript.content_sha256:\n                        issue(\n                            IntegritySeverity.WARNING,\n                            "transcript_changed",\n                            "Файл транскрипта отличается от подтверждённой копии SQLite",\n                            lesson_id=lesson_id,\n                            path=relative,\n                        )\n                except (ContentPathError, OSError) as exc:\n                    issue(\n                        IntegritySeverity.ERROR,\n                        "transcript_read",\n                        str(exc),\n                        lesson_id=lesson_id,\n                        path=content.transcript.relative_path,\n                    )\n\n        for lesson_id in sorted(deleted_ids):\n            if lesson_id not in trash_entries:\n                issue(\n                    IntegritySeverity.ERROR,\n                    "missing_trash_record",\n                    "Удалённое занятие не связано с корзиной",\n                    lesson_id=lesson_id,\n                )\n        if selected is None:\n            for lesson_id, entry in trash_entries.items():\n                path = self.workspace / entry.trash_relative_path\n                if not path.is_dir():\n                    issue(\n                        IntegritySeverity.ERROR,\n                        "missing_trash_directory",\n                        "Каталог занятия отсутствует в корзине",\n                        lesson_id=lesson_id,\n                        path=entry.trash_relative_path,\n                    )\n\n        temporary_paths = [\n            path.relative_to(self.workspace).as_posix() for path in self._temporary_candidates()\n        ]\n        stats.completed_at = datetime.now(UTC)\n        stats.duration_ms = max(0, int((perf_counter() - timer) * 1000))\n        return ContentIntegrityReport(\n            database_ok=database_ok,\n            database_message=database_message,\n            fts_enabled=fts_enabled,\n            fts_documents=fts_documents,\n            indexed_lessons=len(states),\n            lesson_directories=len(lesson_directories),\n            trash_items=len(trash_entries),\n            orphan_directories=sorted(orphan_directories),\n            temporary_paths=temporary_paths,\n            storage=self.storage_usage() if include_storage else StorageUsage(),\n            issues=issues,\n            scan=stats,\n        )\n\n'''
text = replace_regex(
    text,
    r"    def inspect_content_integrity\(self\) -> ContentIntegrityReport:.*?(?=    def rebuild_search_index)",
    new_integrity,
    label="integrity implementation",
)

# Add staged purge helpers and refactor permanent purge.
old_purge = '''    def _permanently_delete_lesson(self, lesson_id: str) -> TrashActionResult:\n        _validate_lesson_id(lesson_id)\n        operation_id = uuid4().hex\n        staging_relative = Path(".trash-purge") / operation_id\n        existing = self.repository.get_trash_entry(lesson_id)\n        if existing is None:\n            raise ContentNotFoundError(f"Занятие не найдено в корзине: {lesson_id}")\n        source = self.workspace / existing.trash_relative_path\n        staging = self.workspace / staging_relative\n        if staging.exists():\n            raise ContentConflictError(f"Временный каталог очистки уже существует: {operation_id}")\n        entry = self.repository.begin_purge(\n            lesson_id,\n            operation_id,\n            staging_relative.as_posix(),\n            datetime.now(UTC),\n        )\n        moved = False\n        database_purged = False\n        try:\n            if source.exists():\n                staging.parent.mkdir(parents=True, exist_ok=True)\n                source.replace(staging)\n                moved = True\n            self.repository.complete_purge_database(lesson_id, operation_id)\n            database_purged = True\n            if staging.exists():\n                shutil.rmtree(staging)\n            self._remove_empty_directory(staging.parent)\n            self.repository.complete_cleanup(operation_id)\n        except Exception as exc:\n            if not moved and not database_purged:\n                self.repository.rollback_purge(lesson_id, operation_id, str(exc))\n            raise\n        return TrashActionResult(\n            lesson_id=lesson_id,\n            size_bytes=entry.size_bytes,\n            operation=ContentOperationKind.PURGE,\n        )\n'''
new_purge = '''    def stage_lesson_purge(\n        self,\n        lesson_id: str,\n    ) -> tuple[TrashActionResult, str, Path]:\n        _validate_lesson_id(lesson_id)\n        operation_id = uuid4().hex\n        staging_relative = Path(".trash-purge") / operation_id\n        existing = self.repository.get_trash_entry(lesson_id)\n        if existing is None:\n            raise ContentNotFoundError(f"Занятие не найдено в корзине: {lesson_id}")\n        source = self.workspace / existing.trash_relative_path\n        staging = self.workspace / staging_relative\n        if staging.exists():\n            raise ContentConflictError(f"Временный каталог очистки уже существует: {operation_id}")\n        entry = self.repository.begin_purge(\n            lesson_id,\n            operation_id,\n            staging_relative.as_posix(),\n            datetime.now(UTC),\n        )\n        moved = False\n        try:\n            if source.exists():\n                staging.parent.mkdir(parents=True, exist_ok=True)\n                source.replace(staging)\n                moved = True\n            self.repository.complete_purge_database(lesson_id, operation_id)\n        except Exception as exc:\n            if not moved:\n                self.repository.rollback_purge(lesson_id, operation_id, str(exc))\n            raise\n        return (\n            TrashActionResult(\n                lesson_id=lesson_id,\n                size_bytes=entry.size_bytes,\n                operation=ContentOperationKind.PURGE,\n            ),\n            operation_id,\n            staging,\n        )\n\n    def finalize_staged_purge(self, operation_id: str, staging: Path) -> None:\n        if staging.exists():\n            shutil.rmtree(staging)\n        self._remove_empty_directory(staging.parent)\n        self.repository.complete_cleanup(operation_id)\n\n    def _permanently_delete_lesson(self, lesson_id: str) -> TrashActionResult:\n        result, operation_id, staging = self.stage_lesson_purge(lesson_id)\n        self.finalize_staged_purge(operation_id, staging)\n        return result\n'''
text = replace_once(text, old_purge, new_purge, label="two phase purge")

new_maintenance = '''    def run_maintenance(\n        self,\n        *,\n        now: datetime | None = None,\n        auto_repair: bool = True,\n        purge_expired: bool = True,\n        cleanup_temporary: bool = True,\n        temporary_retention: timedelta = timedelta(hours=24),\n        backup_enabled: bool = False,\n        backup_interval: timedelta = timedelta(hours=24),\n        backup_retention_count: int = 14,\n        mode: IntegrityCheckMode = IntegrityCheckMode.QUICK,\n        max_lessons: int = 50,\n        max_seconds: int = 120,\n        apply_max_seconds: int = 30,\n    ) -> ContentMaintenanceResult:\n        return self._run_maintenance_cycle(\n            now=now,\n            auto_repair=auto_repair,\n            purge_expired=purge_expired,\n            cleanup_temporary=cleanup_temporary,\n            temporary_retention=temporary_retention,\n            backup_enabled=backup_enabled,\n            backup_interval=backup_interval,\n            backup_retention_count=backup_retention_count,\n            mode=mode,\n            max_lessons=max_lessons,\n            max_seconds=max_seconds,\n            apply_max_seconds=apply_max_seconds,\n        )\n\n    def run_maintenance_uncoordinated(self, **kwargs) -> ContentMaintenanceResult:\n        \"\"\"Compatibility alias; PR 14 owns its short lease phases internally.\"\"\"\n        return self.run_maintenance(**kwargs)\n\n    def _run_maintenance_cycle(\n        self,\n        *,\n        now: datetime | None,\n        auto_repair: bool,\n        purge_expired: bool,\n        cleanup_temporary: bool,\n        temporary_retention: timedelta,\n        backup_enabled: bool,\n        backup_interval: timedelta,\n        backup_retention_count: int,\n        mode: IntegrityCheckMode,\n        max_lessons: int,\n        max_seconds: int,\n        apply_max_seconds: int,\n    ) -> ContentMaintenanceResult:\n        started_at = now or datetime.now(UTC)\n        cycle_timer = perf_counter()\n        result = ContentMaintenanceResult(started_at=started_at, mode=mode)\n        if not self._maintenance_lock.acquire(blocking=False):\n            result.skipped = True\n            result.skip_reason = "Обслуживание уже выполняется в этом процессе"\n            result.completed_at = datetime.now(UTC)\n            return result\n        try:\n            snapshot_timer = perf_counter()\n            row_versions = {\n                lesson_id: self.repository.lesson_row_version(lesson_id)\n                for lesson_id, deleted in self.repository.list_lesson_index_states()\n                if not deleted\n            }\n            result.snapshot_duration_ms = int((perf_counter() - snapshot_timer) * 1000)\n\n            if backup_enabled:\n                try:\n                    backups = self.backups.list()\n                    due = not backups or (started_at - backups[0].manifest.created_at >= backup_interval)\n                    if due:\n                        with self.activity("database-backup"):\n                            result.backup = self.backups.create(reason="scheduled-maintenance")\n                    result.backup_retention = self.backups.prune(backup_retention_count)\n                    result.errors.extend(\n                        f"backup retention: {details}" for details in result.backup_retention.errors\n                    )\n                except Exception as exc:\n                    result.errors.append(f"backup: {exc}")\n                    logging.exception("Не удалось создать резервную копию перед обслуживанием")\n\n            deadline = datetime.now(UTC) + timedelta(seconds=max_seconds)\n            scan_timer = perf_counter()\n            before = self.inspect_content_integrity(\n                mode=mode,\n                deadline=deadline,\n                include_storage=False,\n            )\n            result.scan_duration_ms = int((perf_counter() - scan_timer) * 1000)\n            result.truncated = before.scan.truncated\n\n            targets = sorted(\n                {\n                    item.lesson_id\n                    for item in before.issues\n                    if auto_repair and item.lesson_id and item.code in REPAIRABLE_CONTENT_ISSUES\n                }\n            )\n            expired = (\n                [\n                    item.lesson.lesson_id\n                    for item in self.repository.list_trash_items()\n                    if item.entry.state == TrashState.TRASHED and item.entry.purge_after <= started_at\n                ]\n                if purge_expired\n                else []\n            )\n            temporary = (\n                self._temporary_candidates(now=started_at, minimum_age=temporary_retention)\n                if cleanup_temporary\n                else []\n            )\n            result.planned_repairs = len(targets)\n            result.planned_purges = len(expired)\n            result.planned_temp_cleanup = len(temporary)\n\n            planned = [("repair", item) for item in targets] + [("purge", item) for item in expired]\n            if len(planned) > max_lessons:\n                result.truncated = True\n                result.deferred_actions += len(planned) - max_lessons\n                allowed = planned[:max_lessons]\n                targets = [value for kind, value in allowed if kind == "repair"]\n                expired = [value for kind, value in allowed if kind == "purge"]\n\n            rebuild_fts = auto_repair and before.fts_enabled and any(\n                item.code.startswith("search_index_") for item in before.issues\n            )\n            if not targets and not expired and not temporary and not rebuild_fts:\n                result.report = before\n                return result\n\n            staged: list[tuple[str, Path, TrashActionResult]] = []\n            apply_timer = perf_counter()\n            exclusive_timer = perf_counter()\n            with self.activity(\n                "content-maintenance-apply",\n                exclusive=True,\n                ttl=timedelta(minutes=2),\n            ):\n                self._maintenance_thread_id = get_ident()\n                apply_deadline = perf_counter() + apply_max_seconds\n                active_lesson_ids = {\n                    item.lesson_id\n                    for item in self.active_activities()\n                    if item.lesson_id is not None and item.activity != "content-maintenance-apply"\n                }\n                for lesson_id in targets:\n                    if perf_counter() >= apply_deadline:\n                        result.truncated = True\n                        result.deferred_actions += 1\n                        continue\n                    try:\n                        if lesson_id in active_lesson_ids:\n                            result.stale_actions.append(f"{lesson_id}: active lease")\n                            continue\n                        expected = row_versions.get(lesson_id)\n                        if expected is None or self.repository.lesson_row_version(lesson_id) != expected:\n                            result.stale_actions.append(f"{lesson_id}: row version changed")\n                            continue\n                        content = self.repository.get_content(lesson_id, include_deleted=True)\n                        if content.deleted_at is None and content.lesson.status in VOLATILE_CONTENT_STATUSES:\n                            result.stale_actions.append(f"{lesson_id}: active status")\n                            continue\n                        result.indexed_assets += self._synchronize_lesson_files(lesson_id)\n                        result.repaired_lessons.append(lesson_id)\n                    except Exception as exc:\n                        result.errors.append(f"repair {lesson_id}: {exc}")\n                        logging.exception("Не удалось восстановить занятие: %s", lesson_id)\n                if rebuild_fts:\n                    try:\n                        result.rebuilt_search_documents = self.rebuild_search_index()\n                    except Exception as exc:\n                        result.errors.append(f"search index: {exc}")\n                        logging.exception("Не удалось перестроить FTS во время обслуживания")\n                for lesson_id in expired:\n                    if perf_counter() >= apply_deadline:\n                        result.truncated = True\n                        result.deferred_actions += 1\n                        continue\n                    try:\n                        purge_result, operation_id, staging = self.stage_lesson_purge(lesson_id)\n                        staged.append((operation_id, staging, purge_result))\n                        result.purged_lessons.append(lesson_id)\n                    except Exception as exc:\n                        result.errors.append(f"purge {lesson_id}: {exc}")\n                        logging.exception("Не удалось подготовить очистку корзины: %s", lesson_id)\n            result.exclusive_duration_ms = int((perf_counter() - exclusive_timer) * 1000)\n            self._maintenance_thread_id = None\n\n            for operation_id, staging, _purge_result in staged:\n                try:\n                    self.finalize_staged_purge(operation_id, staging)\n                except Exception as exc:\n                    result.errors.append(f"purge cleanup {operation_id}: {exc}")\n                    logging.exception("Не удалось физически удалить staging очистки: %s", operation_id)\n\n            if temporary:\n                result.temporary_cleanup = self.cleanup_temporary_files(\n                    now=started_at,\n                    minimum_age=temporary_retention,\n                )\n                result.errors.extend(\n                    f"temporary cleanup: {details}" for details in result.temporary_cleanup.errors\n                )\n\n            result.apply_duration_ms = int((perf_counter() - apply_timer) * 1000)\n            result.mutated = bool(\n                result.repaired_lessons\n                or result.purged_lessons\n                or result.temporary_cleanup.removed_paths\n                or result.rebuilt_search_documents is not None\n            )\n            verify_timer = perf_counter()\n            result.report = self.inspect_content_integrity(\n                mode=IntegrityCheckMode.QUICK,\n                lesson_ids=set(result.repaired_lessons),\n                include_storage=False,\n            )\n            result.verify_duration_ms = int((perf_counter() - verify_timer) * 1000)\n            return result\n        finally:\n            result.completed_at = datetime.now(UTC)\n            self._maintenance_thread_id = None\n            self._maintenance_lock.release()\n            logging.info(\n                "Обслуживание архива завершено: mode=%s mutated=%s scan_ms=%s "\n                "exclusive_ms=%s repaired=%s purged=%s errors=%s total_ms=%s",\n                result.mode.value,\n                result.mutated,\n                result.scan_duration_ms,\n                result.exclusive_duration_ms,\n                len(result.repaired_lessons),\n                len(result.purged_lessons),\n                len(result.errors),\n                int((perf_counter() - cycle_timer) * 1000),\n            )\n\n'''
text = replace_regex(
    text,
    r"    def run_maintenance\(.*?(?=    def repair_content_integrity)",
    new_maintenance,
    label="phased maintenance",
)
text = replace_once(
    text,
    "        return self.run_maintenance(\n            auto_repair=True,\n            purge_expired=False,\n            cleanup_temporary=False,\n        )\n",
    "        return self.run_maintenance(\n"
    "            auto_repair=True,\n"
    "            purge_expired=False,\n"
    "            cleanup_temporary=False,\n"
    "            mode=IntegrityCheckMode.FULL,\n"
    "        )\n",
    label="full manual repair",
)
write(path, text)


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/content/__init__.py"
text = read(path)
text = replace_once(
    text,
    "    IndexReport,\n    IntegritySeverity,\n",
    "    IndexReport,\n    IntegrityCheckMode,\n    IntegrityScanStats,\n    IntegritySeverity,\n",
    label="content imports",
)
text = replace_once(
    text,
    '    "IndexReport",\n    "IntegritySeverity",\n',
    '    "IndexReport",\n    "IntegrityCheckMode",\n    "IntegrityScanStats",\n    "IntegritySeverity",\n',
    label="content exports",
)
write(path, text)


# ---------------------------------------------------------------------------
# GUI maintenance now lets the service own short critical sections
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/ui/concurrent_app.py"
text = read(path)
text = replace_once(
    text,
    "                operation=lambda: self.content_service.run_maintenance_uncoordinated(\n",
    "                operation=lambda: self.content_service.run_maintenance(\n",
    label="maintenance call",
)
text = replace_once(
    text,
    "                    backup_retention_count=self.config.content.backup_retention_count,\n                ),\n                activity=\"content-maintenance\",\n                exclusive=True,\n                ttl=timedelta(minutes=5),\n                busy_policy=BusyPolicy.SKIP,\n",
    "                    backup_retention_count=self.config.content.backup_retention_count,\n"
    "                    max_lessons=self.config.content.maintenance_max_lessons_per_cycle,\n"
    "                    max_seconds=self.config.content.maintenance_max_seconds,\n"
    "                    apply_max_seconds=self.config.content.maintenance_apply_max_seconds,\n"
    "                ),\n"
    "                busy_policy=BusyPolicy.SKIP,\n",
    label="remove coarse maintenance lease",
)
write(path, text)


# ---------------------------------------------------------------------------
# CLI modes and budgets
# ---------------------------------------------------------------------------
path = "src/tutor_assistant/cli.py"
text = read(path)
text = replace_once(
    text,
    "    content_doctor.add_argument(\"--strict\", action=\"store_true\")\n",
    "    content_doctor.add_argument(\"--strict\", action=\"store_true\")\n"
    "    content_doctor.add_argument(\"--mode\", choices=(\"quick\", \"full\"), default=\"full\")\n"
    "    content_doctor.add_argument(\"--max-lessons\", type=int)\n"
    "    content_doctor.add_argument(\"--max-seconds\", type=int)\n",
    label="doctor args",
)
text = replace_once(
    text,
    "        from .content import StudentContentService\n\n        service = StudentContentService(\n",
    "        from .content import IntegrityCheckMode, StudentContentService\n\n"
    "        mode = IntegrityCheckMode(args.mode)\n"
    "        service = StudentContentService(\n",
    label="doctor mode import",
)
text = replace_once(
    text,
    "                temporary_retention=timedelta(hours=config.content.temporary_retention_hours),\n            )\n",
    "                temporary_retention=timedelta(hours=config.content.temporary_retention_hours),\n"
    "                mode=mode,\n"
    "                max_lessons=args.max_lessons or config.content.maintenance_max_lessons_per_cycle,\n"
    "                max_seconds=args.max_seconds or config.content.maintenance_max_seconds,\n"
    "                apply_max_seconds=config.content.maintenance_apply_max_seconds,\n"
    "            )\n",
    label="doctor maintenance budgets",
)
text = replace_once(
    text,
    "        report = service.inspect_content_integrity()\n",
    "        report = service.inspect_content_integrity(mode=mode)\n",
    label="doctor report mode",
)
write(path, text)


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------
write(
    "tests/test_content_integrity_cache.py",
    '''from __future__ import annotations\n\nimport os\nfrom datetime import UTC, date, datetime\nfrom pathlib import Path\n\nimport tutor_assistant.content.service as service_module\nfrom tutor_assistant.content import AssetKind, IntegrityCheckMode, StudentContentService\nfrom tutor_assistant.domain import Lesson, Student\n\n\ndef make_lesson(lesson_id: str) -> Lesson:\n    return Lesson(\n        lesson_id=lesson_id,\n        student=Student(id="student", full_name="Ученик"),\n        subject="mathematics",\n        lesson_date=date(2026, 7, 21),\n        topic="Incremental integrity",\n    )\n\n\ndef test_quick_scan_reuses_verified_asset_hash(tmp_path: Path, monkeypatch) -> None:\n    service = StudentContentService(tmp_path / "data")\n    lesson = service.create_lesson(make_lesson("cache-hit"))\n    asset_path = service.workspace / "lessons" / lesson.lesson_id / "handbook" / "lesson.pdf"\n    asset_path.parent.mkdir(parents=True)\n    asset_path.write_bytes(b"pdf-payload")\n    service.register_asset(lesson.lesson_id, asset_path, kind=AssetKind.DOCUMENT)\n\n    calls: list[Path] = []\n    real_hash = service_module._sha256_file\n\n    def tracked(path: Path) -> str:\n        calls.append(path)\n        return real_hash(path)\n\n    monkeypatch.setattr(service_module, "_sha256_file", tracked)\n    first = service.inspect_content_integrity(mode=IntegrityCheckMode.QUICK)\n    first_asset_calls = [path for path in calls if path == asset_path]\n    assert len(first_asset_calls) == 1\n    calls.clear()\n\n    second = service.inspect_content_integrity(mode=IntegrityCheckMode.QUICK)\n    assert asset_path not in calls\n    assert second.scan.asset_cache_hits >= 1\n    assert first.scan.assets_hashed >= 1\n\n\ndef test_full_scan_ignores_cache_and_detects_same_size_same_mtime_change(\n    tmp_path: Path,\n) -> None:\n    service = StudentContentService(tmp_path / "data")\n    lesson = service.create_lesson(make_lesson("full-detect"))\n    asset_path = service.workspace / "lessons" / lesson.lesson_id / "result.bin"\n    asset_path.write_bytes(b"AAAA")\n    service.register_asset(lesson.lesson_id, asset_path, kind=AssetKind.OTHER)\n    service.inspect_content_integrity(mode=IntegrityCheckMode.QUICK)\n    original = asset_path.stat()\n    asset_path.write_bytes(b"BBBB")\n    os.utime(asset_path, ns=(original.st_atime_ns, original.st_mtime_ns))\n\n    quick = service.inspect_content_integrity(mode=IntegrityCheckMode.QUICK)\n    full = service.inspect_content_integrity(mode=IntegrityCheckMode.FULL)\n\n    assert all(issue.code != "asset_changed" for issue in quick.issues)\n    assert any(issue.code == "asset_changed" for issue in full.issues)\n\n\ndef test_migration_seven_is_applied_to_existing_database(tmp_path: Path) -> None:\n    service = StudentContentService(tmp_path / "data")\n    migrations = dict(service.repository.applied_migrations())\n    assert migrations[7] == "asset_verification_cache"\n    with service.repository.connect() as db:\n        columns = {row[1] for row in db.execute("PRAGMA table_info(lesson_assets)")}\n    assert {"file_mtime_ns", "last_verified_at"} <= columns\n''',
)

write(
    "tests/test_content_maintenance_phases.py",
    '''from __future__ import annotations\n\nimport shutil\nfrom datetime import UTC, date, datetime, timedelta\nfrom pathlib import Path\n\nfrom tutor_assistant.content import IntegrityCheckMode, StudentContentService\nfrom tutor_assistant.domain import Lesson, Student\n\n\ndef make_lesson(lesson_id: str) -> Lesson:\n    return Lesson(\n        lesson_id=lesson_id,\n        student=Student(id="student", full_name="Ученик"),\n        subject="mathematics",\n        lesson_date=date(2026, 7, 21),\n        topic="Maintenance phases",\n    )\n\n\ndef test_noop_maintenance_does_not_acquire_exclusive_lease(tmp_path: Path, monkeypatch) -> None:\n    service = StudentContentService(tmp_path / "data")\n    service.create_lesson(make_lesson("noop"))\n    calls: list[tuple[str, bool]] = []\n    real = service.acquire_activity\n\n    def tracked(activity: str, **kwargs):\n        calls.append((activity, bool(kwargs.get("exclusive"))))\n        return real(activity, **kwargs)\n\n    monkeypatch.setattr(service, "acquire_activity", tracked)\n    result = service.run_maintenance(\n        auto_repair=False,\n        purge_expired=False,\n        cleanup_temporary=False,\n    )\n\n    assert not result.mutated\n    assert result.exclusive_duration_ms == 0\n    assert all(not exclusive for _activity, exclusive in calls)\n\n\ndef test_stale_row_version_skips_planned_repair(tmp_path: Path, monkeypatch) -> None:\n    service = StudentContentService(tmp_path / "data")\n    lesson = service.create_lesson(make_lesson("stale-plan"))\n    lesson_json = service.workspace / "lessons" / lesson.lesson_id / "lesson.json"\n    lesson_json.write_text("{}", encoding="utf-8")\n    real_inspect = service.inspect_content_integrity\n    mutated = False\n\n    def inspect_and_mutate(**kwargs):\n        nonlocal mutated\n        report = real_inspect(**kwargs)\n        if not mutated and kwargs.get("lesson_ids") is None:\n            content = service.get_lesson(lesson.lesson_id)\n            changed = content.lesson.model_copy(deep=True)\n            changed.topic = "Changed concurrently"\n            service.update_lesson(changed, expected_row_version=content.row_version)\n            mutated = True\n        return report\n\n    monkeypatch.setattr(service, "inspect_content_integrity", inspect_and_mutate)\n    result = service.run_maintenance(\n        auto_repair=True,\n        purge_expired=False,\n        cleanup_temporary=False,\n        mode=IntegrityCheckMode.FULL,\n    )\n\n    assert result.repaired_lessons == []\n    assert any("row version changed" in item for item in result.stale_actions)\n\n\ndef test_purge_removes_large_staging_after_exclusive_release(tmp_path: Path, monkeypatch) -> None:\n    service = StudentContentService(tmp_path / "data", trash_retention_days=0)\n    lesson = service.create_lesson(make_lesson("two-phase-purge"))\n    service.delete_lesson(lesson.lesson_id)\n    observed_exclusive: list[bool] = []\n    real_rmtree = shutil.rmtree\n\n    def tracked(path, *args, **kwargs):\n        observed_exclusive.append(any(item.exclusive for item in service.active_activities()))\n        return real_rmtree(path, *args, **kwargs)\n\n    monkeypatch.setattr(shutil, "rmtree", tracked)\n    result = service.run_maintenance(\n        now=datetime.now(UTC) + timedelta(seconds=1),\n        auto_repair=False,\n        cleanup_temporary=False,\n    )\n\n    assert result.purged_lessons == [lesson.lesson_id]\n    assert observed_exclusive and observed_exclusive == [False]\n\n\ndef test_maintenance_budget_defers_remaining_actions(tmp_path: Path) -> None:\n    service = StudentContentService(tmp_path / "data")\n    for index in range(3):\n        lesson = service.create_lesson(make_lesson(f"budget-{index}"))\n        (service.workspace / "lessons" / lesson.lesson_id / "lesson.json").write_text(\n            "{}", encoding="utf-8"\n        )\n\n    result = service.run_maintenance(\n        auto_repair=True,\n        purge_expired=False,\n        cleanup_temporary=False,\n        mode=IntegrityCheckMode.FULL,\n        max_lessons=1,\n    )\n\n    assert result.truncated\n    assert result.deferred_actions >= 2\n    assert len(result.repaired_lessons) == 1\n''',
)

# Extend Windows test selection.
path = ".github/workflows/windows-content.yml"
text = read(path)
text = replace_once(
    text,
    "          tests/test_content_coordination.py\n",
    "          tests/test_content_coordination.py\n"
    "          tests/test_content_integrity_cache.py\n"
    "          tests/test_content_maintenance_phases.py\n",
    label="windows storage tests",
)
write(path, text)

print("PR 14 storage patch applied")
