from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tutor_assistant.content import IntegrityCheckMode, StudentContentService
from tutor_assistant.domain import Lesson, Student


def make_lesson(lesson_id: str) -> Lesson:
    return Lesson(
        lesson_id=lesson_id,
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 21),
        topic="Maintenance phases",
    )


def test_noop_maintenance_does_not_acquire_exclusive_lease(tmp_path: Path, monkeypatch) -> None:
    service = StudentContentService(tmp_path / "data")
    service.create_lesson(make_lesson("noop"))
    calls: list[tuple[str, bool]] = []
    real = service.acquire_activity

    def tracked(activity: str, **kwargs):
        calls.append((activity, bool(kwargs.get("exclusive"))))
        return real(activity, **kwargs)

    monkeypatch.setattr(service, "acquire_activity", tracked)
    result = service.run_maintenance(
        auto_repair=False,
        purge_expired=False,
        cleanup_temporary=False,
    )

    assert not result.mutated
    assert result.exclusive_duration_ms == 0
    assert all(not exclusive for _activity, exclusive in calls)


def test_stale_row_version_skips_planned_repair(tmp_path: Path, monkeypatch) -> None:
    service = StudentContentService(tmp_path / "data")
    lesson = service.create_lesson(make_lesson("stale-plan"))
    lesson_json = service.workspace / "lessons" / lesson.lesson_id / "lesson.json"
    lesson_json.write_text("{}", encoding="utf-8")
    real_inspect = service.inspect_content_integrity
    mutated = False

    def inspect_and_mutate(**kwargs):
        nonlocal mutated
        report = real_inspect(**kwargs)
        if not mutated and kwargs.get("lesson_ids") is None:
            content = service.get_lesson(lesson.lesson_id)
            changed = content.lesson.model_copy(deep=True)
            changed.topic = "Changed concurrently"
            service.update_lesson(changed, expected_row_version=content.row_version)
            mutated = True
        return report

    monkeypatch.setattr(service, "inspect_content_integrity", inspect_and_mutate)
    result = service.run_maintenance(
        auto_repair=True,
        purge_expired=False,
        cleanup_temporary=False,
        mode=IntegrityCheckMode.FULL,
    )

    assert result.repaired_lessons == []
    assert any("row version changed" in item for item in result.stale_actions)


def test_purge_removes_large_staging_after_exclusive_release(tmp_path: Path, monkeypatch) -> None:
    service = StudentContentService(tmp_path / "data", trash_retention_days=0)
    lesson = service.create_lesson(make_lesson("two-phase-purge"))
    service.delete_lesson(lesson.lesson_id)
    observed_exclusive: list[bool] = []
    real_rmtree = shutil.rmtree

    def tracked(path, *args, **kwargs):
        observed_exclusive.append(any(item.exclusive for item in service.active_activities()))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", tracked)
    result = service.run_maintenance(
        now=datetime.now(UTC) + timedelta(seconds=1),
        auto_repair=False,
        cleanup_temporary=False,
    )

    assert result.purged_lessons == [lesson.lesson_id]
    assert observed_exclusive and observed_exclusive == [False]


def test_maintenance_budget_defers_remaining_actions(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    for index in range(3):
        lesson = service.create_lesson(make_lesson(f"budget-{index}"))
        (service.workspace / "lessons" / lesson.lesson_id / "lesson.json").write_text("{}", encoding="utf-8")

    result = service.run_maintenance(
        auto_repair=True,
        purge_expired=False,
        cleanup_temporary=False,
        mode=IntegrityCheckMode.FULL,
        max_lessons=1,
    )

    assert result.truncated
    assert result.deferred_actions >= 2
    assert len(result.repaired_lessons) == 1
