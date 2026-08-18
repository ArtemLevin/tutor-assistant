from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LatexMonitorLifecycleState(StrEnum):
    DISABLED = "disabled"
    IDLE = "idle"
    SCANNING = "scanning"


class LatexMonitorScanTrigger(StrEnum):
    MANUAL = "manual"
    PERIODIC = "periodic"
    ENABLE = "enable"


class LatexMonitorScanAction(StrEnum):
    START = "start"
    SKIP_DISABLED = "skip_disabled"
    SKIP_IN_FLIGHT = "skip_in_flight"


@dataclass(frozen=True, slots=True)
class LatexMonitorScanDecision:
    action: LatexMonitorScanAction
    trigger: LatexMonitorScanTrigger

    @property
    def should_start(self) -> bool:
        return self.action == LatexMonitorScanAction.START


class LatexMonitorCoordinator:
    """Qt-free lifecycle policy for polling remote LaTeX branches."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = bool(enabled)
        self._scan_in_flight = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def scan_in_flight(self) -> bool:
        return self._scan_in_flight

    @property
    def state(self) -> LatexMonitorLifecycleState:
        if self._scan_in_flight:
            return LatexMonitorLifecycleState.SCANNING
        if self._enabled:
            return LatexMonitorLifecycleState.IDLE
        return LatexMonitorLifecycleState.DISABLED

    def set_enabled(self, enabled: bool) -> LatexMonitorLifecycleState:
        self._enabled = bool(enabled)
        return self.state

    def request_scan(
        self,
        trigger: LatexMonitorScanTrigger,
    ) -> LatexMonitorScanDecision:
        if self._scan_in_flight:
            return LatexMonitorScanDecision(
                LatexMonitorScanAction.SKIP_IN_FLIGHT,
                trigger,
            )
        if trigger != LatexMonitorScanTrigger.MANUAL and not self._enabled:
            return LatexMonitorScanDecision(
                LatexMonitorScanAction.SKIP_DISABLED,
                trigger,
            )
        self._scan_in_flight = True
        return LatexMonitorScanDecision(LatexMonitorScanAction.START, trigger)

    def finish_scan(self) -> LatexMonitorLifecycleState:
        self._scan_in_flight = False
        return self.state
