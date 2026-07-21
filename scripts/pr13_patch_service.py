from __future__ import annotations

from pathlib import Path

path = Path("src/tutor_assistant/content/service.py")
text = path.read_text(encoding="utf-8")

old = "from copy import deepcopy\nfrom datetime import UTC, date, datetime, timedelta\n"
new = "from copy import deepcopy\nfrom dataclasses import dataclass\nfrom datetime import UTC, date, datetime, timedelta\n"
assert text.count(old) == 1
text = text.replace(old, new)

old = "class ContentPathError(ValueError):\n    pass\n\n\ndef _sha256_file"
new = '''class ContentPathError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ActivityAcquireResult:
    lease: ActivityLease | None
    blockers: tuple[ActivityLeaseInfo, ...] = ()

    @property
    def acquired(self) -> bool:
        return self.lease is not None


def _sha256_file'''
assert text.count(old) == 1
text = text.replace(old, new)

start = text.index("    def acquire_activity(\n")
end = text.index("    @contextmanager\n", start)
replacement = '''    def try_acquire_activity(
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
        return ActivityAcquireResult(
            lease=ActivityLease(self.lease_store, result.lease_info, ttl),
        )

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

'''
text = text[:start] + replacement + text[end:]

start = text.index("    def run_maintenance(\n")
end = text.index("    def repair_content_integrity", start)
replacement = '''    def run_maintenance(
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
    ) -> ContentMaintenanceResult:
        """Run one coordinated, failure-isolated archive maintenance cycle."""

        return self._run_maintenance_cycle(
            now=now,
            auto_repair=auto_repair,
            purge_expired=purge_expired,
            cleanup_temporary=cleanup_temporary,
            temporary_retention=temporary_retention,
            backup_enabled=backup_enabled,
            backup_interval=backup_interval,
            backup_retention_count=backup_retention_count,
            acquire_lease=True,
        )

    def run_maintenance_uncoordinated(
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
    ) -> ContentMaintenanceResult:
        """Run maintenance under a lease already owned by an external coordinator."""

        return self._run_maintenance_cycle(
            now=now,
            auto_repair=auto_repair,
            purge_expired=purge_expired,
            cleanup_temporary=cleanup_temporary,
            temporary_retention=temporary_retention,
            backup_enabled=backup_enabled,
            backup_interval=backup_interval,
            backup_retention_count=backup_retention_count,
            acquire_lease=False,
        )

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
        acquire_lease: bool,
    ) -> ContentMaintenanceResult:
        started_at = now or datetime.now(UTC)
        result = ContentMaintenanceResult(started_at=started_at)
        if not self._maintenance_lock.acquire(blocking=False):
            result.skipped = True
            result.skip_reason = "Обслуживание уже выполняется в этом процессе"
            result.completed_at = datetime.now(UTC)
            return result
        maintenance_lease: ActivityLease | None = None
        try:
            if acquire_lease:
                acquisition = self.try_acquire_activity(
                    "content-maintenance",
                    exclusive=True,
                    ttl=timedelta(minutes=5),
                )
                if acquisition.lease is None:
                    result.skipped = True
                    result.skip_reason = str(ContentBusyError.from_blockers(acquisition.blockers))
                    return result
                maintenance_lease = acquisition.lease
            self._maintenance_thread_id = get_ident()

            if backup_enabled:
                try:
                    backups = self.backups.list()
                    due = not backups or (started_at - backups[0].manifest.created_at >= backup_interval)
                    if due:
                        result.backup = self.backups.create(reason="scheduled-maintenance")
                    result.backup_retention = self.backups.prune(backup_retention_count)
                    result.errors.extend(
                        f"backup retention: {details}" for details in result.backup_retention.errors
                    )
                except Exception as exc:
                    result.errors.append(f"backup: {exc}")
                    logging.exception("Не удалось создать резервную копию перед обслуживанием")
                    return result

            try:
                before = self.inspect_content_integrity()
            except Exception as exc:
                result.errors.append(f"diagnostics: {exc}")
                logging.exception("Не удалось проверить архив перед обслуживанием")
                return result

            if auto_repair:
                targets = sorted(
                    {
                        item.lesson_id
                        for item in before.issues
                        if item.lesson_id and item.code in REPAIRABLE_CONTENT_ISSUES
                    }
                )
                for lesson_id in targets:
                    try:
                        content = self.repository.get_content(lesson_id, include_deleted=True)
                        if content.deleted_at is None and content.lesson.status in VOLATILE_CONTENT_STATUSES:
                            continue
                        result.indexed_assets += self._synchronize_lesson_files(lesson_id)
                        result.repaired_lessons.append(lesson_id)
                    except Exception as exc:
                        result.errors.append(f"repair {lesson_id}: {exc}")
                        logging.exception("Не удалось восстановить занятие: %s", lesson_id)

                if before.fts_enabled and any(
                    item.code.startswith("search_index_") for item in before.issues
                ):
                    try:
                        result.rebuilt_search_documents = self.rebuild_search_index()
                    except Exception as exc:
                        result.errors.append(f"search index: {exc}")
                        logging.exception("Не удалось перестроить FTS во время обслуживания")

            if purge_expired:
                expired = [
                    item.lesson.lesson_id
                    for item in self.repository.list_trash_items()
                    if item.entry.state == TrashState.TRASHED and item.entry.purge_after <= started_at
                ]
                for lesson_id in expired:
                    try:
                        self.permanently_delete_lesson(lesson_id)
                        result.purged_lessons.append(lesson_id)
                    except Exception as exc:
                        result.errors.append(f"purge {lesson_id}: {exc}")
                        logging.exception("Не удалось автоматически очистить корзину: %s", lesson_id)

            if cleanup_temporary:
                result.temporary_cleanup = self.cleanup_temporary_files(
                    now=started_at,
                    minimum_age=temporary_retention,
                )
                result.errors.extend(
                    f"temporary cleanup: {details}" for details in result.temporary_cleanup.errors
                )

            try:
                result.report = self.inspect_content_integrity()
            except Exception as exc:
                result.errors.append(f"final diagnostics: {exc}")
                logging.exception("Не удалось проверить архив после обслуживания")
            return result
        finally:
            result.completed_at = datetime.now(UTC)
            if maintenance_lease is not None:
                maintenance_lease.release()
            self._maintenance_thread_id = None
            self._maintenance_lock.release()
            logging.info(
                "Обслуживание архива завершено: repaired=%s assets=%s purged=%s temporary=%s errors=%s",
                len(result.repaired_lessons),
                result.indexed_assets,
                len(result.purged_lessons),
                len(result.temporary_cleanup.removed_paths),
                len(result.errors),
            )

'''
text = text[:start] + replacement + text[end:]

path.write_text(text, encoding="utf-8")
