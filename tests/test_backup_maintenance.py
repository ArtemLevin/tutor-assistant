from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tutor_assistant.application.backup_maintenance import (
    SCHEDULED_BACKUP_REASONS,
    BackupAction,
    BackupMaintenanceCoordinator,
)
from tutor_assistant.cli import parser
from tutor_assistant.content import DatabaseBackupVerification, StudentContentService


def coordinator(
    service: StudentContentService,
    *,
    enabled: bool = True,
    retention: int = 2,
) -> BackupMaintenanceCoordinator:
    return BackupMaintenanceCoordinator(
        service,
        service.workspace,
        enabled=enabled,
        interval_hours=24,
        retention_count=retention,
    )


def test_scheduled_backup_is_verified_persisted_and_not_due_after_success(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path)
    maintenance = coordinator(service)

    assert maintenance.decide().action is BackupAction.RUN
    snapshot = maintenance.run_due()

    assert snapshot.verified
    assert snapshot.last_error is None
    assert snapshot.scheduled_copy_count == 1
    assert maintenance.decide().action is BackupAction.NOT_DUE
    saved = json.loads(maintenance.status_path.read_text(encoding="utf-8"))
    assert saved["verified"] is True
    assert saved["running"] is False


def test_disabled_recording_and_shutdown_prevent_backup(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path)

    assert coordinator(service, enabled=False).decide().action is BackupAction.DISABLED
    maintenance = coordinator(service)
    assert maintenance.decide(recording_active=True).action is BackupAction.BLOCKED
    with service.activity("recording", lesson_id="active"):
        assert maintenance.decide().action is BackupAction.BLOCKED
    maintenance.request_shutdown()
    assert maintenance.decide().action is BackupAction.SHUTDOWN
    assert service.list_database_backups() == []


def test_failed_create_never_prunes_existing_safety_copy(tmp_path: Path, monkeypatch) -> None:
    service = StudentContentService(tmp_path)
    safety = service.create_database_backup(reason="pre-restore-safety")
    maintenance = coordinator(service, retention=1)

    def fail_create(*, reason: str):
        raise OSError(f"synthetic create failure: {reason}")

    monkeypatch.setattr(service, "create_database_backup", fail_create)

    snapshot = maintenance.run_due()

    assert snapshot.last_error and "synthetic create failure" in snapshot.last_error
    assert safety.path.is_file()
    assert safety.manifest_path.is_file()


def test_backup_errors_are_redacted_in_persisted_status(tmp_path: Path, monkeypatch) -> None:
    service = StudentContentService(tmp_path)
    maintenance = coordinator(service)

    def fail_create(*, reason: str):
        raise RuntimeError(f"{reason}: Authorization: Api-Key backup-secret-value")

    monkeypatch.setattr(service, "create_database_backup", fail_create)

    result = maintenance.run_due()

    assert "backup-secret-value" not in (result.last_error or "")
    assert "backup-secret-value" not in maintenance.status_path.read_text(encoding="utf-8")


def test_failed_verification_never_invokes_retention(tmp_path: Path, monkeypatch) -> None:
    service = StudentContentService(tmp_path)
    maintenance = coordinator(service)
    retention_calls = 0

    def reject(path: Path) -> DatabaseBackupVerification:
        return DatabaseBackupVerification(path=path, valid=False, errors=["synthetic SHA mismatch"])

    def prune(*_args, **_kwargs):
        nonlocal retention_calls
        retention_calls += 1

    monkeypatch.setattr(service, "verify_database_backup", reject)
    monkeypatch.setattr(service, "prune_database_backups", prune)

    snapshot = maintenance.run_due()

    assert "synthetic SHA mismatch" in (snapshot.last_error or "")
    assert retention_calls == 0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("format_version", 999, "версия backup manifest"),
        ("schema_version", 999, "schema version"),
    ],
)
def test_backup_verification_rejects_unknown_format_or_schema(
    tmp_path: Path,
    field: str,
    value: int,
    message: str,
) -> None:
    service = StudentContentService(tmp_path)
    backup = service.create_database_backup(reason="manual")
    manifest = json.loads(backup.manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    backup.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verification = service.verify_database_backup(backup.path)

    assert not verification.valid
    assert any(message in error for error in verification.errors)


def test_scheduled_retention_preserves_manual_and_safety_classes(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path)
    safety = service.create_database_backup(reason="pre-restore-safety")
    upgrade = service.create_database_backup(reason="pre-upgrade")
    manual = service.create_database_backup(reason="manual")
    maintenance = coordinator(service, retention=2)

    for offset in range(4):
        maintenance.run_due(now=datetime.now(UTC) + timedelta(days=offset + 1))

    backups = service.list_database_backups()
    scheduled = [item for item in backups if item.manifest.reason in SCHEDULED_BACKUP_REASONS]
    assert len(scheduled) == 2
    assert safety.path.is_file()
    assert upgrade.path.is_file()
    assert manual.path.is_file()


def test_restart_recovers_verified_backup_state(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path)
    initial = coordinator(service)
    successful = initial.run_due()

    restored = coordinator(service)

    assert restored.snapshot().last_backup_id == successful.last_backup_id
    assert restored.snapshot().verified
    assert restored.decide().action is BackupAction.NOT_DUE


def test_single_flight_blocks_a_second_concurrent_backup(tmp_path: Path, monkeypatch) -> None:
    service = StudentContentService(tmp_path)
    maintenance = coordinator(service)
    original = service.create_database_backup
    entered = threading.Event()
    release = threading.Event()

    def delayed(*, reason: str):
        entered.set()
        assert release.wait(timeout=5)
        return original(reason=reason)

    monkeypatch.setattr(service, "create_database_backup", delayed)
    thread = threading.Thread(target=maintenance.run_due)
    thread.start()
    assert entered.wait(timeout=5)

    assert maintenance.decide().action is BackupAction.RUNNING
    assert maintenance.run_due().running
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(service.list_database_backups()) == 1


def test_backup_application_layer_does_not_depend_on_qt() -> None:
    source = (
        Path(__file__).parents[1] / "src" / "tutor_assistant" / "application" / "backup_maintenance.py"
    ).read_text(encoding="utf-8")

    assert "PySide6" not in source
    assert "tutor_assistant.ui" not in source


def test_cli_supports_protected_pre_upgrade_backup_reason() -> None:
    arguments = parser().parse_args(["content-backup", "--create", "--reason", "pre-upgrade"])

    assert arguments.create
    assert arguments.reason == "pre-upgrade"


@pytest.mark.parametrize("value", [0, -1])
def test_invalid_backup_policy_is_rejected(tmp_path: Path, value: int) -> None:
    service = StudentContentService(tmp_path)

    with pytest.raises(ValueError):
        BackupMaintenanceCoordinator(
            service,
            tmp_path,
            enabled=True,
            interval_hours=value,
            retention_count=1,
        )
