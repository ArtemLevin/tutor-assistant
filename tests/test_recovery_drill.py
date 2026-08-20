from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tutor_assistant.content import StudentContentService
from tutor_assistant.domain import Lesson, Student
from tutor_assistant.recovery_drill import run_recovery_drill, write_recovery_drill_report


def test_recovery_drill_covers_restore_quarantine_rollback_and_audio(tmp_path: Path) -> None:
    workspace = tmp_path / "live"
    service = StudentContentService(workspace)
    service.create_lesson(
        Lesson(
            lesson_id="real-private-lesson",
            student=Student(id="real-student", full_name="Private Student Name"),
            subject="mathematics",
            topic="Private lesson topic",
            lesson_date=date(2026, 8, 20),
        )
    )
    before = {
        path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()
    }

    report = run_recovery_drill(workspace)

    after = {
        path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()
    }
    assert report.passed
    assert report.live_workspace_unchanged
    assert before == after
    checks = {item.name: item.passed for item in report.checks}
    for name in (
        "database_backup",
        "backup_verification",
        "database_restore",
        "post_backup_quarantine",
        "rollback_to_safety_database",
        "malformed_manifest_rejected",
        "sha_mismatch_and_corrupt_sqlite_rejected",
        "quarantine_restore_collision_rejected",
        "offline_corrupted_database_restore",
        "dual_channel_recovery",
        "microphone_only_recovery",
        "system_only_recovery",
        "missing_audio_explicitly_rejected",
        "malformed_recording_metadata_recovered",
    ):
        assert checks[name], name
    encoded = json.dumps(report.to_dict())
    assert "Private Student Name" not in encoded
    assert "Private lesson topic" not in encoded


def test_recovery_evidence_cannot_be_written_into_live_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "live"
    report = run_recovery_drill(workspace)

    with pytest.raises(ValueError, match="outside"):
        write_recovery_drill_report(report, workspace / "report.json", live_workspace=workspace)

    target = write_recovery_drill_report(
        report,
        tmp_path / "reports" / "recovery.json",
        live_workspace=workspace,
    )
    assert json.loads(target.read_text(encoding="utf-8"))["passed"]
