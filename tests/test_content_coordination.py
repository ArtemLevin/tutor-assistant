from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tutor_assistant.content import ContentBusyError, StudentContentService
from tutor_assistant.content.coordination import ActivityLease, ActivityLeaseStore


def test_shared_leases_are_compatible_and_block_exclusive(tmp_path: Path) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    first = store.try_acquire(owner_id="owner-a", activity="read-a")
    second = store.try_acquire(owner_id="owner-b", activity="read-b")

    assert first.acquired
    assert second.acquired

    blocked = store.try_acquire(
        owner_id="owner-c",
        activity="maintenance",
        exclusive=True,
    )

    assert not blocked.acquired
    assert {item.activity for item in blocked.blockers} == {"read-a", "read-b"}
    assert all(not item.exclusive for item in blocked.blockers)

    store.release(first.lease_info.lease_id, "owner-a")
    store.release(second.lease_info.lease_id, "owner-b")


def test_shared_request_returns_only_exclusive_blockers(tmp_path: Path) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    exclusive = store.try_acquire(
        owner_id="owner-a",
        activity="content-maintenance",
        exclusive=True,
    )
    assert exclusive.acquired

    blocked = store.try_acquire(owner_id="owner-b", activity="latex-monitor")

    assert not blocked.acquired
    assert [item.activity for item in blocked.blockers] == ["content-maintenance"]
    assert blocked.blockers[0].exclusive

    store.release(exclusive.lease_info.lease_id, "owner-a")


def test_stale_lease_is_removed_before_conflict_check(tmp_path: Path) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    expired = datetime.now(UTC) - timedelta(minutes=1)
    with store._connect() as db:
        db.execute(
            """
            INSERT INTO activity_leases (
                lease_id, owner_id, activity, lesson_id, exclusive,
                acquired_at, heartbeat_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "expired",
                "dead-process",
                "content-maintenance",
                None,
                1,
                expired.isoformat(),
                expired.isoformat(),
                expired.isoformat(),
            ),
        )

    result = store.try_acquire(
        owner_id="live-process",
        activity="database-restore",
        exclusive=True,
    )

    assert result.acquired
    assert result.blockers == ()
    assert [item.activity for item in store.active()] == ["database-restore"]
    store.release(result.lease_info.lease_id, "live-process")


def test_service_try_acquire_is_non_throwing_and_legacy_api_is_structured(
    tmp_path: Path,
) -> None:
    service = StudentContentService(tmp_path / "data")
    blocker = service.acquire_activity("content-maintenance", exclusive=True)
    try:
        result = service.try_acquire_activity("latex-monitor")

        assert not result.acquired
        assert result.lease is None
        assert [item.activity for item in result.blockers] == ["content-maintenance"]

        with pytest.raises(ContentBusyError) as captured:
            service.acquire_activity("latex-monitor")

        assert [item.activity for item in captured.value.blockers] == ["content-maintenance"]
        assert "content-maintenance" in str(captured.value)
    finally:
        blocker.release()


def test_activity_lease_heartbeat_and_idempotent_release(tmp_path: Path) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    acquired = store.try_acquire(
        owner_id="owner",
        activity="long-operation",
        ttl=timedelta(seconds=3),
    )
    assert acquired.lease_info is not None
    lease = ActivityLease(store, acquired.lease_info, timedelta(seconds=3))
    initial_heartbeat = acquired.lease_info.heartbeat_at

    time.sleep(1.2)
    active = store.active()

    assert len(active) == 1
    assert active[0].heartbeat_at > initial_heartbeat
    lease.release()
    lease.release()
    assert store.active() == []


def test_uncoordinated_maintenance_uses_existing_coordinator_lease(
    tmp_path: Path,
) -> None:
    service = StudentContentService(tmp_path / "data")
    with service.activity(
        "content-maintenance",
        exclusive=True,
        ttl=timedelta(minutes=5),
    ):
        result = service.run_maintenance_uncoordinated(
            auto_repair=False,
            purge_expired=False,
            cleanup_temporary=False,
        )

    assert not result.skipped
    assert result.completed_at is not None


def test_public_maintenance_preserves_skip_contract_when_workspace_is_busy(
    tmp_path: Path,
) -> None:
    first = StudentContentService(tmp_path / "data")
    second = StudentContentService(tmp_path / "data")
    blocker = first.acquire_activity("recording", lesson_id="lesson")
    try:
        result = second.run_maintenance(
            auto_repair=False,
            purge_expired=False,
            cleanup_temporary=False,
        )
    finally:
        blocker.release()

    assert result.skipped
    assert "recording" in (result.skip_reason or "")
