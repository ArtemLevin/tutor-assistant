from __future__ import annotations

from tutor_assistant.application.latex_monitor import (
    LatexMonitorCoordinator,
    LatexMonitorLifecycleState,
    LatexMonitorScanAction,
    LatexMonitorScanTrigger,
)


def test_periodic_scan_is_blocked_while_monitor_is_disabled() -> None:
    coordinator = LatexMonitorCoordinator()

    decision = coordinator.request_scan(LatexMonitorScanTrigger.PERIODIC)

    assert decision.action == LatexMonitorScanAction.SKIP_DISABLED
    assert not decision.should_start
    assert coordinator.state == LatexMonitorLifecycleState.DISABLED


def test_manual_scan_is_allowed_while_monitor_is_disabled() -> None:
    coordinator = LatexMonitorCoordinator()

    decision = coordinator.request_scan(LatexMonitorScanTrigger.MANUAL)

    assert decision.should_start
    assert coordinator.scan_in_flight
    assert coordinator.state == LatexMonitorLifecycleState.SCANNING
    assert coordinator.finish_scan() == LatexMonitorLifecycleState.DISABLED


def test_enabled_monitor_allows_periodic_scan() -> None:
    coordinator = LatexMonitorCoordinator()

    assert coordinator.set_enabled(True) == LatexMonitorLifecycleState.IDLE
    decision = coordinator.request_scan(LatexMonitorScanTrigger.PERIODIC)

    assert decision.should_start
    assert coordinator.state == LatexMonitorLifecycleState.SCANNING
    assert coordinator.finish_scan() == LatexMonitorLifecycleState.IDLE


def test_single_flight_rejects_second_scan_regardless_of_trigger() -> None:
    coordinator = LatexMonitorCoordinator(enabled=True)
    first = coordinator.request_scan(LatexMonitorScanTrigger.PERIODIC)

    second = coordinator.request_scan(LatexMonitorScanTrigger.MANUAL)

    assert first.should_start
    assert second.action == LatexMonitorScanAction.SKIP_IN_FLIGHT
    assert coordinator.scan_in_flight


def test_disabling_monitor_during_scan_does_not_release_single_flight() -> None:
    coordinator = LatexMonitorCoordinator(enabled=True)
    coordinator.request_scan(LatexMonitorScanTrigger.PERIODIC)

    assert coordinator.set_enabled(False) == LatexMonitorLifecycleState.SCANNING
    assert not coordinator.enabled
    assert coordinator.request_scan(LatexMonitorScanTrigger.MANUAL).action == (
        LatexMonitorScanAction.SKIP_IN_FLIGHT
    )
    assert coordinator.finish_scan() == LatexMonitorLifecycleState.DISABLED


def test_enable_trigger_requires_enabled_monitor() -> None:
    coordinator = LatexMonitorCoordinator()

    blocked = coordinator.request_scan(LatexMonitorScanTrigger.ENABLE)
    coordinator.set_enabled(True)
    started = coordinator.request_scan(LatexMonitorScanTrigger.ENABLE)

    assert blocked.action == LatexMonitorScanAction.SKIP_DISABLED
    assert started.action == LatexMonitorScanAction.START
