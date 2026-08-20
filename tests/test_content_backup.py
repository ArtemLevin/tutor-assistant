from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

import tutor_assistant.content.backup as backup_module
import tutor_assistant.content.safe_service as safe_service_module
from tutor_assistant.cli import main
from tutor_assistant.config import AppConfig
from tutor_assistant.content import (
    ContentBusyError,
    DatabaseBackupError,
    StudentContentService,
)
from tutor_assistant.domain import Lesson, Student


def lesson(identifier: str, topic: str = "Исходная тема") -> Lesson:
    return Lesson(
        lesson_id=identifier,
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 18),
        topic=topic,
    )


def test_online_backup_restore_recovers_database_and_file_projection(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    created = service.create_lesson(lesson("restore-me"))
    service.save_transcript(created.lesson_id, "Версия из backup")
    backup = service.create_database_backup(reason="test")

    current = service.get_lesson(created.lesson_id)
    changed = current.lesson.model_copy(deep=True)
    changed.topic = "Изменённая тема"
    service.update_lesson(changed, expected_row_version=current.row_version)
    service.save_transcript(created.lesson_id, "Версия после backup")

    restored = service.restore_database_backup(backup.path)

    content = service.get_lesson(created.lesson_id)
    assert restored.verified
    assert restored.safety_backup.manifest.reason == "pre-restore-safety"
    assert content.lesson.topic == "Исходная тема"
    assert content.transcript is not None
    assert content.transcript.content == "Версия из backup\n"
    projection = workspace / "lessons" / created.lesson_id / "transcript" / "transcript_verified.txt"
    assert projection.read_text(encoding="utf-8") == "Версия из backup\n"


def test_successful_restore_quarantines_lessons_created_after_backup(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    service.create_lesson(lesson("before-backup"))
    backup = service.create_database_backup(reason="before-new-lesson")
    service.create_lesson(lesson("after-backup"))
    post_backup_directory = workspace / "lessons" / "after-backup"
    assert post_backup_directory.is_dir()

    service.restore_database_backup(backup.path)

    assert service.repository.get_lesson("after-backup", include_deleted=True) is None
    assert not post_backup_directory.exists()
    manifests = list((workspace / ".restore-quarantine").glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert [item["lesson_id"] for item in manifest["lessons"]] == ["after-backup"]
    quarantined = manifests[0].parent / "lessons" / "after-backup"
    assert quarantined.is_dir()
    assert (quarantined / "lesson.json").is_file()


def test_restore_failure_rolls_back_database_and_filesystem_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    created = service.create_lesson(lesson("restore-rollback"))
    service.save_transcript(created.lesson_id, "Старая версия из backup")
    backup = service.create_database_backup(reason="old-state")

    current = service.get_lesson(created.lesson_id)
    changed = current.lesson.model_copy(deep=True)
    changed.topic = "Текущее состояние"
    service.update_lesson(changed, expected_row_version=current.row_version)
    service.save_transcript(created.lesson_id, "Текущая версия")
    post_backup = service.create_lesson(lesson("rollback-post-backup"))
    post_backup_directory = workspace / "lessons" / post_backup.lesson_id
    projection = workspace / "lessons" / created.lesson_id / "transcript" / "transcript_verified.txt"
    assert projection.read_text(encoding="utf-8") == "Текущая версия\n"
    assert post_backup_directory.is_dir()

    original_sync = service._synchronize_lesson_files
    calls = 0

    def fail_after_first_projection(lesson_id: str, *, project_assets: bool = True) -> int:
        nonlocal calls
        calls += 1
        result = original_sync(lesson_id, project_assets=project_assets)
        if calls == 1:
            raise RuntimeError("fault after restored projection")
        return result

    monkeypatch.setattr(service, "_synchronize_lesson_files", fail_after_first_projection)

    with pytest.raises(RuntimeError, match="fault after restored projection"):
        service.restore_database_backup(backup.path)

    content = service.get_lesson(created.lesson_id)
    assert content.lesson.topic == "Текущее состояние"
    assert content.transcript is not None
    assert content.transcript.content == "Текущая версия\n"
    assert projection.read_text(encoding="utf-8") == "Текущая версия\n"
    assert service.get_lesson(post_backup.lesson_id).lesson.lesson_id == post_backup.lesson_id
    assert post_backup_directory.is_dir()
    assert not list((workspace / ".restore-quarantine").glob("*/manifest.json"))
    assert calls >= 2


def test_corrupted_backup_is_rejected_without_changing_live_database(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    service.create_lesson(lesson("still-live"))
    backup = service.create_database_backup(reason="test")
    backup.path.write_bytes(backup.path.read_bytes()[:128] + b"corrupted")

    verification = service.verify_database_backup(backup.path)

    assert not verification.valid
    assert any("SHA-256" in error or "SQLite" in error for error in verification.errors)
    with pytest.raises(DatabaseBackupError, match="не прошла проверку"):
        service.restore_database_backup(backup.path)
    assert service.get_lesson("still-live").lesson.lesson_id == "still-live"


def test_backup_disappearing_during_checksum_verification_returns_invalid_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = StudentContentService(tmp_path / "data")
    backup = service.create_database_backup(reason="race")

    def remove_during_checksum(path: Path) -> str:
        path.unlink()
        raise FileNotFoundError("backup disappeared during verification")

    monkeypatch.setattr(backup_module, "_sha256_file", remove_during_checksum)

    verification = service.verify_database_backup(backup.path)

    assert not verification.valid
    assert any("недоступ" in error for error in verification.errors)


@pytest.mark.parametrize(
    ("change", "expected_error"),
    [
        ("missing", "Manifest отсутствует или повреждён"),
        ("malformed", "Manifest отсутствует или повреждён"),
        ("filename", "Manifest относится к другому файлу"),
        ("size", "Размер не совпадает"),
        ("checksum", "Контрольная сумма SHA-256 не совпадает"),
    ],
)
def test_backup_verification_rejects_invalid_manifest_without_mutating_live_data(
    tmp_path: Path,
    change: str,
    expected_error: str,
) -> None:
    service = StudentContentService(tmp_path / "data")
    service.create_lesson(lesson("live-lesson"))
    backup = service.create_database_backup(reason="integrity")

    if change == "missing":
        backup.manifest_path.unlink()
    elif change == "malformed":
        backup.manifest_path.write_text("{not-valid-json", encoding="utf-8")
    else:
        manifest = json.loads(backup.manifest_path.read_text(encoding="utf-8"))
        if change == "filename":
            manifest["database_file"] = "another.sqlite3"
        elif change == "size":
            manifest["size_bytes"] += 1
        else:
            manifest["sha256"] = "0" * 64
        backup.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verification = service.verify_database_backup(backup.path)

    assert not verification.valid
    assert any(expected_error in error for error in verification.errors)
    with pytest.raises(DatabaseBackupError, match="не прошла проверку"):
        service.restore_database_backup(backup.path)
    assert service.get_lesson("live-lesson").lesson.lesson_id == "live-lesson"


def test_backup_verification_detects_concurrent_file_change(tmp_path: Path, monkeypatch) -> None:
    service = StudentContentService(tmp_path / "data")
    backup = service.create_database_backup(reason="race")
    original_hash = backup_module._sha256_file

    def hash_and_change_timestamp(path: Path) -> str:
        digest = original_hash(path)
        current = path.stat()
        os.utime(path, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000_000))
        return digest

    monkeypatch.setattr(backup_module, "_sha256_file", hash_and_change_timestamp)

    verification = service.verify_database_backup(backup.path)

    assert not verification.valid
    assert "Резервная копия изменилась во время проверки" in verification.errors


def test_failed_backup_manifest_write_cleans_database_and_temporary_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = StudentContentService(tmp_path / "data")

    def fail_manifest_write(_path: Path, _payload: str) -> None:
        raise PermissionError("manifest destination is read-only")

    monkeypatch.setattr(backup_module, "atomic_write_text", fail_manifest_write)

    with pytest.raises(PermissionError, match="read-only"):
        service.create_database_backup(reason="manifest-failure")

    assert list(service.backups.directory.iterdir()) == []


def test_failed_restore_and_rollback_preserve_both_errors_and_safety_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = StudentContentService(tmp_path / "data")
    service.create_lesson(lesson("recoverable"))
    backup = service.create_database_backup(reason="known-good")

    def fail_restore(path: Path) -> None:
        if path == backup.path:
            raise RuntimeError("primary restore failed")
        raise OSError("safety rollback failed")

    monkeypatch.setattr(service, "_restore_database_file", fail_restore)

    with pytest.raises(DatabaseBackupError, match="safety rollback") as captured:
        service.restore_database_backup(backup.path)

    assert isinstance(captured.value.__cause__, ExceptionGroup)
    assert [str(error) for error in captured.value.__cause__.exceptions] == [
        "primary restore failed",
        "safety rollback failed",
    ]
    safety = [
        item for item in service.list_database_backups() if item.manifest.reason == "pre-restore-safety"
    ]
    assert len(safety) == 1
    assert safety[0].path.is_file()


def test_failed_quarantine_manifest_restores_post_backup_lesson_directories(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    service.create_lesson(lesson("before-backup"))
    backup = service.create_database_backup(reason="known-good")
    service.create_lesson(lesson("after-backup"))
    post_backup_directory = workspace / "lessons" / "after-backup"

    def fail_quarantine_manifest(_path: Path, _payload: str) -> None:
        raise PermissionError("quarantine manifest write failed")

    monkeypatch.setattr(safe_service_module, "atomic_write_text", fail_quarantine_manifest)

    with pytest.raises(PermissionError, match="quarantine manifest"):
        service.restore_database_backup(backup.path)

    assert service.get_lesson("before-backup").lesson.lesson_id == "before-backup"
    assert service.get_lesson("after-backup").lesson.lesson_id == "after-backup"
    assert post_backup_directory.is_dir()
    assert (post_backup_directory / "lesson.json").is_file()


@pytest.mark.parametrize("keep", [0, -1])
def test_backup_retention_never_accepts_policy_that_removes_all_copies(
    tmp_path: Path,
    keep: int,
) -> None:
    service = StudentContentService(tmp_path / "data")
    backup = service.create_database_backup(reason="manual")

    with pytest.raises(ValueError, match="хотя бы одна"):
        service.backups.prune(keep)

    assert backup.path.is_file()
    assert backup.manifest_path.is_file()


def test_backup_retention_removes_only_old_recognized_pairs(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    service.create_lesson(lesson("retention"))
    first = service.create_database_backup(reason="first")
    second = service.create_database_backup(reason="second")
    unrelated = service.backups.directory / "keep-me.txt"
    unrelated.write_text("unmanaged", encoding="utf-8")

    result = service.prune_database_backups(1)

    assert result.errors == []
    assert result.removed == [first.path]
    assert not first.path.exists()
    assert not first.manifest_path.exists()
    assert second.path.exists()
    assert unrelated.exists()


def test_second_process_is_blocked_during_exclusive_operation(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    first = StudentContentService(workspace)
    second = StudentContentService(workspace)
    second.create_lesson(lesson("protected-from-delete"))

    with first.activity("recording", lesson_id="active-lesson"):
        maintenance = second.run_maintenance()
        assert maintenance.skipped
        assert maintenance.skip_reason and "recording" in maintenance.skip_reason
        with pytest.raises(ContentBusyError, match="recording"):
            second.create_database_backup()

    backup = second.create_database_backup()
    assert backup.path.is_file()

    with first.activity("database-restore", exclusive=True):
        with pytest.raises(ContentBusyError, match="database-restore"):
            second.delete_lesson("protected-from-delete")


def test_expired_lease_does_not_permanently_lock_workspace(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    info = service.lease_store.acquire(
        owner_id="crashed-process",
        activity="recording",
        exclusive=False,
        ttl=timedelta(microseconds=1),
    )
    assert info is not None

    with service.activity("database-backup", exclusive=True):
        assert [item.activity for item in service.active_activities()] == ["database-backup"]


def test_scheduled_maintenance_creates_backup_before_destructive_work(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data", trash_retention_days=0)
    created = service.create_lesson(lesson("purge-after-backup"))
    service.delete_lesson(created.lesson_id)

    result = service.run_maintenance(
        now=datetime.now(UTC) + timedelta(seconds=1),
        auto_repair=False,
        backup_enabled=True,
        backup_interval=timedelta(hours=24),
        backup_retention_count=2,
    )

    assert result.errors == []
    assert result.backup is not None
    assert result.backup.path.exists()
    assert result.purged_lessons == [created.lesson_id]


def test_content_backup_cli_create_list_verify_and_restore_guard(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config_path = tmp_path / "app.yaml"
    config = AppConfig(workspace=tmp_path / "data")
    config.save(config_path)

    monkeypatch.setattr(
        sys,
        "argv",
        ["tutor-assistant", "--config", str(config_path), "content-backup", "--create"],
    )
    main()
    created = json.loads(capsys.readouterr().out)
    backup_path = Path(created["path"])

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tutor-assistant",
            "--config",
            str(config_path),
            "content-backup",
            "--verify",
            str(backup_path),
        ],
    )
    main()
    assert json.loads(capsys.readouterr().out)["valid"] is True

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tutor-assistant",
            "--config",
            str(config_path),
            "content-backup",
            "--restore",
            str(backup_path),
        ],
    )
    with pytest.raises(SystemExit, match="--yes"):
        main()


def test_cli_can_restore_when_live_database_is_corrupted(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config_path = tmp_path / "app.yaml"
    config = AppConfig(workspace=tmp_path / "data")
    config.save(config_path)
    service = StudentContentService(config.workspace)
    service.create_lesson(lesson("recover-corrupt-live"))
    backup = service.create_database_backup(reason="before-corruption")
    database = config.workspace / "tutor-assistant.sqlite3"
    Path(str(database) + "-wal").unlink(missing_ok=True)
    Path(str(database) + "-shm").unlink(missing_ok=True)
    database.write_bytes(b"\xff" * 4096)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tutor-assistant",
            "--config",
            str(config_path),
            "content-backup",
            "--restore",
            str(backup.path),
            "--yes",
        ],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["verified"] is True
    assert payload["raw_safety_path"]
    recovered = StudentContentService(config.workspace)
    assert recovered.get_lesson("recover-corrupt-live").lesson.lesson_id == "recover-corrupt-live"
