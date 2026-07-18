from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tutor_assistant.cli import main
from tutor_assistant.config import AppConfig
from tutor_assistant.content import (
    AssetKind,
    IntegritySeverity,
    LessonFilters,
    StudentContentService,
)
from tutor_assistant.domain import Lesson, Student


def lesson(identifier: str, *, topic: str = "Квадратные уравнения") -> Lesson:
    return Lesson(
        lesson_id=identifier,
        student=Student(id="timofey", full_name="Тимофей Иванов"),
        subject="mathematics",
        lesson_date=date(2026, 7, 18),
        topic=topic,
    )


def test_full_text_search_covers_metadata_transcript_and_safe_special_characters(
    tmp_path: Path,
) -> None:
    service = StudentContentService(tmp_path / "data")
    service.create_lesson(lesson("fts-lesson"))
    service.save_transcript("fts-lesson", "Дискриминант и теорема Виета")

    assert service.list_lessons(LessonFilters(query="Тимофей")).total == 1
    assert service.list_lessons(LessonFilters(query="квадрат")).total == 1
    assert service.list_lessons(LessonFilters(query="теорема Виета")).total == 1
    assert service.list_lessons(LessonFilters(query='"Виета" - ()')).total == 1
    assert service.list_lessons(LessonFilters(query="несуществующее")).total == 0

    service.save_transcript("fts-lesson", "Новая тема: производная")

    assert service.list_lessons(LessonFilters(query="производн")).total == 1
    assert service.list_lessons(LessonFilters(query="дискриминант")).total == 0


def test_search_fallback_includes_transcript_when_fts_is_unavailable(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    service.create_lesson(lesson("fallback"))
    service.save_transcript("fallback", "Тригонометрическая окружность")
    with service.repository.connect() as db:
        db.execute("UPDATE content_capabilities SET enabled=0 WHERE name='fts5'")

    result = service.list_lessons(LessonFilters(query="окружность"))

    assert [item.lesson_id for item in result.items] == ["fallback"]
    assert service.list_lessons(LessonFilters(query='"окружность" - ()')).total == 1


def test_integrity_report_finds_changed_files_orphans_and_search_drift(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    source = workspace / "lessons" / "healthy" / "recording" / "lesson.wav"
    service.create_lesson(lesson("healthy"))
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original-audio")
    service.register_asset("healthy", source, kind=AssetKind.AUDIO)
    source.write_bytes(b"changed-audio")
    orphan = workspace / "lessons" / "lost-folder"
    orphan.mkdir(parents=True)
    with service.repository.connect() as db:
        if service.repository._fts_available(db):
            db.execute("DELETE FROM lesson_search WHERE lesson_id='healthy'")

    report = service.inspect_content_integrity()

    codes = {issue.code for issue in report.issues}
    assert report.database_ok
    assert "asset_changed" in codes
    assert "orphan_directory" in codes
    if report.fts_enabled:
        assert "search_index_missing" in codes
    assert "lessons/lost-folder" in report.orphan_directories
    assert any(issue.severity == IntegritySeverity.WARNING for issue in report.issues)
    assert orphan.is_dir()

    rebuilt = service.repository.rebuild_search_index()
    repaired = service.inspect_content_integrity()

    if repaired.fts_enabled:
        assert rebuilt == 1
        assert repaired.fts_documents == repaired.indexed_lessons


def test_cleanup_removes_only_old_known_temporary_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    old_import = workspace / ".import-staging" / "abandoned"
    old_import.mkdir(parents=True)
    (old_import / "audio.part").write_bytes(b"stale")
    old_tmp = workspace / "lessons" / "lesson" / "lesson.json.tmp"
    old_tmp.parent.mkdir(parents=True)
    old_tmp.write_bytes(b"temporary")
    fresh_part = workspace / "lessons" / "lesson" / "audio.part"
    fresh_part.write_bytes(b"active")
    unrelated = workspace / "lessons" / "lesson" / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    old_timestamp = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(old_import, (old_timestamp, old_timestamp))
    os.utime(old_tmp, (old_timestamp, old_timestamp))

    before = service.inspect_content_integrity()
    result = service.cleanup_temporary_files()

    assert set(before.temporary_paths) == {
        ".import-staging/abandoned",
        "lessons/lesson/lesson.json.tmp",
    }
    assert set(result.removed_paths) == set(before.temporary_paths)
    assert result.released_bytes > 0
    assert result.errors == []
    assert not old_import.exists()
    assert not old_tmp.exists()
    assert fresh_part.exists()
    assert unrelated.exists()


def test_cleanup_preserves_staging_of_incomplete_trash_operation(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    service.create_lesson(lesson("protected-purge"))
    service.delete_lesson("protected-purge")
    relative = ".trash-purge/protected-operation"
    service.repository.begin_purge(
        "protected-purge",
        "protected-operation",
        relative,
        datetime.now(UTC),
    )
    protected = workspace / relative
    protected.mkdir(parents=True)
    (protected / "data.bin").write_bytes(b"do-not-delete")
    old_timestamp = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(protected, (old_timestamp, old_timestamp))

    result = service.cleanup_temporary_files()

    assert result.removed_paths == []
    assert protected.is_dir()


def test_storage_usage_separates_lessons_trash_temporary_database_and_free_space(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    service.create_lesson(lesson("storage"))
    payload = workspace / "lessons" / "storage" / "recording" / "lesson.wav"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"audio-payload")
    service.delete_lesson("storage")
    temporary = workspace / ".import-staging" / "stale"
    temporary.mkdir(parents=True)
    (temporary / "file.part").write_bytes(b"temporary")

    usage = service.storage_usage()

    assert usage.lessons_bytes == 0
    assert usage.trash_bytes >= len(b"audio-payload")
    assert usage.temporary_bytes >= len(b"temporary")
    assert usage.database_bytes > 0
    assert usage.managed_bytes > usage.database_bytes
    assert usage.free_bytes > 0


def test_content_doctor_cli_returns_machine_readable_report(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config_path = tmp_path / "app.yaml"
    AppConfig(workspace=tmp_path / "data").save(config_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["tutor-assistant", "--config", str(config_path), "content-doctor", "--json"],
    )

    main()
    payload = json.loads(capsys.readouterr().out)

    assert payload["database_ok"] is True
    assert payload["healthy"] is True
    assert payload["storage"]["free_bytes"] > 0
    assert payload["storage"]["managed_bytes"] > 0
    assert payload["cleanup"] is None
