from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tutor_assistant.content import ContentBusyError, LeaseState, StudentContentService
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
    renewed = threading.Event()
    original_heartbeat = store.heartbeat

    def observe_heartbeat(lease_id: str, owner_id: str, ttl: timedelta) -> bool:
        result = original_heartbeat(lease_id, owner_id, ttl)
        renewed.set()
        return result

    store.heartbeat = observe_heartbeat
    lease = ActivityLease(store, acquired.lease_info, timedelta(seconds=3))
    initial_heartbeat = acquired.lease_info.heartbeat_at

    assert renewed.wait(timeout=3)
    active = store.active()

    assert len(active) == 1
    assert active[0].heartbeat_at > initial_heartbeat
    assert lease.state == LeaseState.HEALTHY
    assert lease.valid
    lease.release()
    lease.release()
    assert lease.state == LeaseState.RELEASED
    assert not lease.valid
    assert store.active() == []


@pytest.mark.parametrize("ttl", [timedelta(0), timedelta(seconds=-1)])
def test_invalid_heartbeat_ttl_cannot_expire_an_active_lease(
    tmp_path: Path,
    ttl: timedelta,
) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    acquired = store.try_acquire(owner_id="owner", activity="recording")
    assert acquired.lease_info is not None

    with pytest.raises(ValueError, match="TTL must be positive"):
        store.heartbeat(acquired.lease_info.lease_id, "owner", ttl)

    assert [item.lease_id for item in store.active()] == [acquired.lease_info.lease_id]
    store.release(acquired.lease_info.lease_id, "owner")


def test_concurrent_lease_release_invokes_cleanup_only_once(tmp_path: Path, monkeypatch) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    acquired = store.try_acquire(owner_id="owner", activity="recording")
    assert acquired.lease_info is not None
    callbacks: list[str] = []
    first_entered = threading.Event()
    second_started = threading.Event()
    allow_release = threading.Event()
    original_release = store.release

    def controlled_release(lease_id: str, owner_id: str) -> None:
        first_entered.set()
        assert allow_release.wait(timeout=3)
        original_release(lease_id, owner_id)

    monkeypatch.setattr(store, "release", controlled_release)
    lease = ActivityLease(
        store,
        acquired.lease_info,
        timedelta(minutes=2),
        on_release=lambda released: callbacks.append(released.info.lease_id),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(lease.release)
        assert first_entered.wait(timeout=2)

        def release_again() -> None:
            second_started.set()
            lease.release()

        second = executor.submit(release_again)
        assert second_started.wait(timeout=2)
        allow_release.set()
        first.result(timeout=3)
        second.result(timeout=3)

    assert callbacks == [acquired.lease_info.lease_id]
    assert lease.state == LeaseState.RELEASED
    assert store.active() == []


def test_short_activity_lease_is_renewed_before_expiration(tmp_path: Path) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    ttl = timedelta(milliseconds=600)
    acquired = store.try_acquire(owner_id="owner", activity="short-operation", ttl=ttl)
    assert acquired.lease_info is not None
    renewed = threading.Event()
    original_heartbeat = store.heartbeat

    def observe_heartbeat(lease_id: str, owner_id: str, current_ttl: timedelta) -> bool:
        result = original_heartbeat(lease_id, owner_id, current_ttl)
        renewed.set()
        return result

    store.heartbeat = observe_heartbeat

    lease = ActivityLease(store, acquired.lease_info, ttl)
    try:
        assert renewed.wait(timeout=2)
        active = store.active()
        assert len(active) == 1
        assert active[0].heartbeat_at > acquired.lease_info.heartbeat_at
        assert lease.valid
    finally:
        lease.release()


def test_activity_lease_marks_rejected_heartbeat_as_lost(tmp_path: Path, monkeypatch) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    acquired = store.try_acquire(
        owner_id="owner",
        activity="recording",
        ttl=timedelta(seconds=3),
    )
    assert acquired.lease_info is not None
    monkeypatch.setattr(store, "heartbeat", lambda *_args, **_kwargs: False)
    lease = ActivityLease(store, acquired.lease_info, timedelta(seconds=3))

    assert lease._stop.wait(timeout=2)

    assert lease.state == LeaseState.LOST
    assert not lease.valid
    assert "rejected" in (lease.lost_reason or "")
    assert lease.origin_thread_id == -1
    lease.release()
    assert lease.state == LeaseState.RELEASED


def test_activity_lease_marks_heartbeat_exception_as_lost(tmp_path: Path, monkeypatch) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    acquired = store.try_acquire(
        owner_id="owner",
        activity="recording",
        ttl=timedelta(seconds=3),
    )
    assert acquired.lease_info is not None

    def fail_heartbeat(*_args, **_kwargs):
        raise RuntimeError("operations database unavailable")

    monkeypatch.setattr(store, "heartbeat", fail_heartbeat)
    lease = ActivityLease(store, acquired.lease_info, timedelta(seconds=3))

    assert lease._stop.wait(timeout=2)

    assert lease.state == LeaseState.LOST
    assert "database unavailable" in (lease.lost_reason or "")
    lease.release()


@pytest.mark.parametrize("ttl", [timedelta(0), timedelta(seconds=-1)])
def test_lease_acquisition_rejects_non_positive_ttl_without_creating_blocker(
    tmp_path: Path,
    ttl: timedelta,
) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")

    with pytest.raises(ValueError, match="TTL must be positive"):
        store.try_acquire(owner_id="owner", activity="recording", ttl=ttl)

    assert store.active() == []


def test_wrong_owner_cannot_renew_or_release_another_process_lease(tmp_path: Path) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    acquired = store.try_acquire(owner_id="actual-owner", activity="recording")
    assert acquired.lease_info is not None

    assert not store.heartbeat(acquired.lease_info.lease_id, "other-owner", timedelta(minutes=1))
    store.release(acquired.lease_info.lease_id, "other-owner")

    active = store.active()
    assert len(active) == 1
    assert active[0].lease_id == acquired.lease_info.lease_id
    assert active[0].owner_id == "actual-owner"
    store.release(acquired.lease_info.lease_id, "actual-owner")


def test_lesson_scoped_leases_block_same_lesson_without_blocking_others(tmp_path: Path) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    first = store.try_acquire(owner_id="first", activity="recording", lesson_id="lesson-1")
    assert first.lease_info is not None

    same_lesson = store.try_acquire(
        owner_id="second",
        activity="pipeline-write",
        lesson_id="lesson-1",
    )
    different_lesson = store.try_acquire(
        owner_id="third",
        activity="pipeline-write",
        lesson_id="lesson-2",
    )

    assert not same_lesson.acquired
    assert [item.activity for item in same_lesson.blockers] == ["recording"]
    assert different_lesson.lease_info is not None
    store.release(first.lease_info.lease_id, "first")
    store.release(different_lesson.lease_info.lease_id, "third")


def test_workspace_generation_is_shared_and_monotonically_advanced(tmp_path: Path) -> None:
    first = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    second = ActivityLeaseStore(tmp_path / "operations.sqlite3")

    initial = first.generation()
    assert second.generation() == initial
    assert first.advance_generation() == initial + 1
    assert second.generation() == initial + 1
    assert second.advance_generation() == initial + 2
    assert first.generation() == initial + 2


def test_lost_owned_lease_does_not_bypass_write_coordination(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    lease = service.acquire_activity("recording", lesson_id="lesson")
    lease._mark_lost("simulated heartbeat loss")

    assert not service._current_thread_lease_protects("lesson")
    lease.release()


def test_owned_lease_delegation_is_limited_to_one_worker_scope(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    lease = service.acquire_activity("recording", lesson_id="lesson")

    with ThreadPoolExecutor(max_workers=2) as executor:

        def unrelated_worker() -> bool:
            assert not service._current_thread_lease_protects("lesson")
            with pytest.raises(ContentBusyError, match="recording"):
                with service._write_activity("pipeline-write", lesson_id="lesson"):
                    raise AssertionError("unrelated worker bypassed the recording lease")
            return True

        def finalizing_worker() -> bool:
            assert not service._current_thread_lease_protects("lesson")
            with pytest.raises(ContentBusyError, match="recording"):
                with service._write_activity("pipeline-write", lesson_id="lesson"):
                    raise AssertionError("worker bypassed the lease before delegation")

            with service.use_owned_activity_lease(lease, lesson_id="lesson"):
                assert service._current_thread_lease_protects("lesson")
                with service._write_activity("pipeline-write", lesson_id="lesson"):
                    assert executor.submit(unrelated_worker).result(timeout=5)
                    exclusive = service.try_acquire_activity(
                        "database-backup",
                        exclusive=True,
                    )
                    assert not exclusive.acquired
                    assert [item.activity for item in exclusive.blockers] == ["recording"]

            assert not service._current_thread_lease_protects("lesson")
            return True

        try:
            assert executor.submit(finalizing_worker).result(timeout=10)
            assert service._current_thread_lease_protects("lesson")
        finally:
            lease.release()


def test_owned_lease_delegation_rejects_other_lesson_owner_and_lost_lease(
    tmp_path: Path,
) -> None:
    service = StudentContentService(tmp_path / "data")
    other_owner = StudentContentService(tmp_path / "data")
    lease = service.acquire_activity("recording", lesson_id="lesson")
    try:
        with pytest.raises(ContentBusyError, match="не защищает указанное занятие"):
            with service.use_owned_activity_lease(lease, lesson_id="other-lesson"):
                raise AssertionError("lease protected an unrelated lesson")

        with pytest.raises(ContentBusyError, match="не принадлежит операции"):
            with other_owner.use_owned_activity_lease(lease, lesson_id="lesson"):
                raise AssertionError("lease was delegated to another owner")

        lease._mark_lost("simulated heartbeat loss")
        with pytest.raises(ContentBusyError, match="не принадлежит операции"):
            with service.use_owned_activity_lease(lease, lesson_id="lesson"):
                raise AssertionError("lost lease protected the worker")
    finally:
        lease.release()


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
