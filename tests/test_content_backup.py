from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

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
