from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import tutor_assistant.content.service as content_service_module
from tutor_assistant.cli import main
from tutor_assistant.config import AppConfig
from tutor_assistant.content import LessonFilters, StudentContentService
from tutor_assistant.domain import JobStatus, Lesson, Student


def make_lesson(lesson_id: str) -> Lesson:
    return Lesson(
        lesson_id=lesson_id,
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 18),
        topic="Эксплуатационный контроль архива",
    )


def test_doctor_detects_and_repairs_every_derived_projection(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    lesson = service.create_lesson(make_lesson("repair-projections"))
    service.save_transcript(lesson.lesson_id, "Подтверждённая производная")
    directory = workspace / "lessons" / lesson.lesson_id
    stale = Lesson.read_json(directory / "lesson.json")
    stale.topic = "Устаревшая карточка"
    stale.write_json(directory / "lesson.json")
    transcript = directory / "transcript" / "transcript_verified.txt"
    transcript.write_text("Повреждённый текст", encoding="utf-8")
    unregistered = directory / "handbook" / "lesson.pdf"
    unregistered.parent.mkdir(parents=True)
    unregistered.write_bytes(b"%PDF-unregistered")
    with service.repository.connect() as db:
        service.repository._mark_file_sync(db, lesson.lesson_id)
        if service.repository._fts_available(db):
            db.execute(
                "UPDATE lesson_search SET metadata='stale' WHERE lesson_id=?",
                (lesson.lesson_id,),
            )
    service.repository.fail_file_sync(lesson.lesson_id, "simulated Windows lock")

    before = service.inspect_content_integrity()
    codes = {issue.code for issue in before.issues}

    assert {
        "failed_file_sync",
        "lesson_payload_mismatch",
        "unregistered_asset",
        "transcript_changed",
    } <= codes
    if before.fts_enabled:
        assert "search_index_stale" in codes

    result = service.repair_content_integrity()

    after = service.inspect_content_integrity()
    remaining = {issue.code for issue in after.issues}
    assert result.errors == []
    assert result.repaired_lessons == [lesson.lesson_id]
    assert service.repository.pending_file_sync() == []
    assert Lesson.read_json(directory / "lesson.json").topic == lesson.topic
    assert transcript.read_text(encoding="utf-8") == "Подтверждённая производная\n"
    assert any(
        asset.relative_path.endswith("lesson.pdf") for asset in service.get_lesson(lesson.lesson_id).assets
    )
    assert not (
        {
            "failed_file_sync",
            "lesson_payload_mismatch",
            "unregistered_asset",
            "transcript_changed",
            "search_index_stale",
        }
        & remaining
    )
    assert service.list_lessons(LessonFilters(query="подтверждённая производная")).total == 1


def test_maintenance_purges_expired_trash_and_old_temporary_files(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace, trash_retention_days=0)
    lesson = service.create_lesson(make_lesson("expired-trash"))
    service.delete_lesson(lesson.lesson_id)
    temporary = workspace / ".import-staging" / "expired"
    temporary.mkdir(parents=True)
    (temporary / "payload.part").write_bytes(b"temporary")
    old = (datetime.now(UTC) - timedelta(hours=3)).timestamp()
    os.utime(temporary, (old, old))

    result = service.run_maintenance(
        now=datetime.now(UTC) + timedelta(seconds=1),
        auto_repair=False,
        temporary_retention=timedelta(hours=1),
    )

    assert result.errors == []
    assert result.purged_lessons == [lesson.lesson_id]
    assert result.temporary_cleanup.removed_paths == [".import-staging/expired"]
    assert service.repository.get_lesson(lesson.lesson_id, include_deleted=True) is None
    assert not (workspace / "trash" / "lessons" / lesson.lesson_id).exists()
    assert not temporary.exists()


def test_repair_failure_is_isolated_and_other_lessons_continue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = StudentContentService(tmp_path / "data")
    failed = service.create_lesson(make_lesson("failed-repair"))
    healthy = service.create_lesson(make_lesson("healthy-repair"))
    for lesson in (failed, healthy):
        document = service.workspace / "lessons" / lesson.lesson_id / "result.pdf"
        document.write_bytes(lesson.lesson_id.encode())
    real_sync = service._synchronize_lesson_files

    def injected_failure(lesson_id: str, *, project_assets: bool = True) -> int:
        if lesson_id == failed.lesson_id:
            raise PermissionError("simulated locked lesson")
        return real_sync(lesson_id, project_assets=project_assets)

    monkeypatch.setattr(service, "_synchronize_lesson_files", injected_failure)

    result = service.run_maintenance(
        auto_repair=True,
        purge_expired=False,
        cleanup_temporary=False,
    )

    assert result.repaired_lessons == [healthy.lesson_id]
    assert any("simulated locked lesson" in error for error in result.errors)
    assert any(
        asset.relative_path.endswith("result.pdf") for asset in service.get_lesson(healthy.lesson_id).assets
    )
    assert all(
        not asset.relative_path.endswith("result.pdf")
        for asset in service.get_lesson(failed.lesson_id).assets
    )


def test_purge_failure_does_not_block_other_purges_or_temp_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace, trash_retention_days=0)
    failed = service.create_lesson(make_lesson("failed-purge"))
    healthy = service.create_lesson(make_lesson("healthy-purge"))
    service.delete_lesson(failed.lesson_id)
    service.delete_lesson(healthy.lesson_id)
    temporary = workspace / ".import-staging" / "cleanup-after-failure"
    temporary.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(temporary, (old, old))
    real_purge = service.permanently_delete_lesson

    def injected_failure(lesson_id: str):
        if lesson_id == failed.lesson_id:
            raise PermissionError("simulated purge lock")
        return real_purge(lesson_id)

    monkeypatch.setattr(service, "permanently_delete_lesson", injected_failure)

    result = service.run_maintenance(
        now=datetime.now(UTC) + timedelta(seconds=1),
        auto_repair=False,
    )

    assert result.purged_lessons == [healthy.lesson_id]
    assert any("simulated purge lock" in error for error in result.errors)
    assert not temporary.exists()
    assert service.repository.get_lesson(failed.lesson_id, include_deleted=True) is not None
    assert service.repository.get_lesson(healthy.lesson_id, include_deleted=True) is None


def test_doctor_skips_files_owned_by_active_pipeline_stage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = StudentContentService(tmp_path / "data")
    lesson = service.create_lesson(make_lesson("active-stage"))
    recording = service.workspace / "lessons" / lesson.lesson_id / "recording" / "mixed.wav"
    recording.parent.mkdir(parents=True)
    recording.write_bytes(b"locked")
    lesson.transition(JobStatus.RECORDING)
    service.persist_pipeline_lesson(lesson, frozenset({"status", "error"}))
    real_sha256 = content_service_module._sha256_file

    def reject_recording(path: Path) -> str:
        if path == recording:
            raise PermissionError("active file must not be read")
        return real_sha256(path)

    monkeypatch.setattr(content_service_module, "_sha256_file", reject_recording)

    report = service.inspect_content_integrity()

    assert any(issue.code == "active_lesson_skipped" for issue in report.issues)
    assert all(
        issue.relative_path != recording.relative_to(service.workspace).as_posix() for issue in report.issues
    )


def test_overlapping_maintenance_cycle_is_skipped(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    service._maintenance_lock.acquire()
    try:
        result = service.run_maintenance()
    finally:
        service._maintenance_lock.release()

    assert result.skipped
    assert result.completed_at is not None


def test_content_doctor_cli_can_repair_and_purge(tmp_path: Path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "app.yaml"
    config = AppConfig(workspace=tmp_path / "data")
    config.content.trash_retention_days = 0
    config.save(config_path)
    service = StudentContentService(config.workspace, trash_retention_days=0)
    lesson = service.create_lesson(make_lesson("cli-maintenance"))
    service.delete_lesson(lesson.lesson_id)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tutor-assistant",
            "--config",
            str(config_path),
            "content-doctor",
            "--json",
            "--repair",
            "--purge-expired",
        ],
    )

    main()
    payload = json.loads(capsys.readouterr().out)

    assert payload["maintenance"]["purged_lessons"] == [lesson.lesson_id]
    assert payload["maintenance"]["errors"] == []
