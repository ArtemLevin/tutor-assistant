from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import tutor_assistant.content.service as service_module
from tutor_assistant.content import AssetKind, IntegrityCheckMode, StudentContentService
from tutor_assistant.domain import Lesson, Student


def make_lesson(lesson_id: str) -> Lesson:
    return Lesson(
        lesson_id=lesson_id,
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 21),
        topic="Incremental integrity",
    )


def test_quick_scan_reuses_verified_asset_hash(tmp_path: Path, monkeypatch) -> None:
    service = StudentContentService(tmp_path / "data")
    lesson = service.create_lesson(make_lesson("cache-hit"))
    asset_path = service.workspace / "lessons" / lesson.lesson_id / "handbook" / "lesson.pdf"
    asset_path.parent.mkdir(parents=True)
    asset_path.write_bytes(b"pdf-payload")
    service.register_asset(lesson.lesson_id, asset_path, kind=AssetKind.DOCUMENT)

    calls: list[Path] = []
    real_hash = service_module._sha256_file

    def tracked(path: Path) -> str:
        calls.append(path)
        return real_hash(path)

    monkeypatch.setattr(service_module, "_sha256_file", tracked)
    first = service.inspect_content_integrity(mode=IntegrityCheckMode.QUICK)
    first_asset_calls = [path for path in calls if path == asset_path]
    assert len(first_asset_calls) == 1
    calls.clear()

    second = service.inspect_content_integrity(mode=IntegrityCheckMode.QUICK)
    assert asset_path not in calls
    assert second.scan.asset_cache_hits >= 1
    assert first.scan.assets_hashed >= 1


def test_full_scan_ignores_cache_and_detects_same_size_same_mtime_change(
    tmp_path: Path,
) -> None:
    service = StudentContentService(tmp_path / "data")
    lesson = service.create_lesson(make_lesson("full-detect"))
    asset_path = service.workspace / "lessons" / lesson.lesson_id / "result.bin"
    asset_path.write_bytes(b"AAAA")
    service.register_asset(lesson.lesson_id, asset_path, kind=AssetKind.OTHER)
    service.inspect_content_integrity(mode=IntegrityCheckMode.QUICK)
    original = asset_path.stat()
    asset_path.write_bytes(b"BBBB")
    os.utime(asset_path, ns=(original.st_atime_ns, original.st_mtime_ns))

    quick = service.inspect_content_integrity(mode=IntegrityCheckMode.QUICK)
    full = service.inspect_content_integrity(mode=IntegrityCheckMode.FULL)

    assert all(issue.code != "asset_changed" for issue in quick.issues)
    assert any(issue.code == "asset_changed" for issue in full.issues)


def test_migration_seven_is_applied_to_existing_database(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    migrations = dict(service.repository.applied_migrations())
    assert migrations[7] == "asset_verification_cache"
    with service.repository.connect() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(lesson_assets)")}
    assert {"file_mtime_ns", "last_verified_at"} <= columns
