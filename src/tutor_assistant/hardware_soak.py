"""Privacy-safe hardware acceptance evidence and strict release thresholds."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_text
from .runtime import build_identity

REQUIRED_SCENARIOS = (
    "long_recording",
    "repeated_lifecycle",
    "microphone_disconnect_reconnect",
    "playback_endpoint_change",
    "forced_process_termination",
    "single_channel_degradation",
    "parallel_review",
    "background_workload",
)
NUMERIC_METRICS = (
    "cumulative_recording_seconds",
    "longest_recording_seconds",
    "start_stop_cycles",
    "forced_recovery_cases",
    "device_disruption_cases",
    "lost_recoverable_recordings",
    "unexplained_unhandled_crashes",
    "captured_blocks",
    "dropped_blocks",
    "queue_high_water_mark",
    "writer_latency_ms",
    "reconnect_count",
    "silence_periods",
    "stream_exceptions",
    "finalize_duration_ms",
    "output_size_bytes",
    "sample_rate_hz",
    "process_memory_start_bytes",
    "process_memory_end_bytes",
    "memory_growth_bytes",
)


@dataclass(frozen=True, slots=True)
class HardwareSoakReport:
    created_at: str
    build: dict[str, object]
    metrics: dict[str, int | float]
    scenarios: dict[str, bool]
    thresholds: dict[str, int]
    failures: tuple[str, ...]
    passed: bool

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["failures"] = list(self.failures)
        return payload


def _number(value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return 0
    return value


def collect_hardware_observations(workspace: Path) -> dict[str, object]:
    metrics: dict[str, int | float] = dict.fromkeys(NUMERIC_METRICS, 0)
    fingerprints: set[str] = set()
    for manifest in workspace.expanduser().glob("lessons/*/recording/session.json"):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        duration = _number(payload.get("duration_seconds", 0))
        if not duration:
            try:
                started = datetime.fromisoformat(str(payload["started_at"]))
                finished = datetime.fromisoformat(str(payload.get("completed_at", payload["updated_at"])))
                duration = max(0, int((finished - started).total_seconds()))
            except (KeyError, TypeError, ValueError):
                duration = 0
        metrics["cumulative_recording_seconds"] += duration
        metrics["longest_recording_seconds"] = max(metrics["longest_recording_seconds"], duration)
        if payload.get("status") in {"recorded", "completed"}:
            metrics["start_stop_cycles"] += 1
        for source in ("microphone", "system"):
            metrics["captured_blocks"] += _number(payload.get(f"{source}_chunks", 0))
            metrics["dropped_blocks"] += _number(payload.get(f"{source}_dropped_blocks", 0))
        device = payload.get("system_device_id")
        if isinstance(device, str) and device:
            fingerprints.add(hashlib.sha256(device.encode("utf-8")).hexdigest()[:16])
        output = manifest.parent / "lesson.wav"
        if output.is_file():
            metrics["output_size_bytes"] += output.stat().st_size
    return {"metrics": metrics, "device_fingerprints": sorted(fingerprints), "scenarios": {}}


def evaluate_hardware_soak(observations: dict[str, Any]) -> HardwareSoakReport:
    supplied = observations.get("metrics", {})
    if not isinstance(supplied, dict):
        supplied = {}
    metrics = {name: _number(supplied.get(name, 0)) for name in NUMERIC_METRICS}
    supplied_scenarios = observations.get("scenarios", {})
    if not isinstance(supplied_scenarios, dict):
        supplied_scenarios = {}
    scenarios = {name: supplied_scenarios.get(name) is True for name in REQUIRED_SCENARIOS}
    thresholds = {
        "cumulative_recording_seconds": 20 * 3600,
        "longest_recording_seconds": 2 * 3600,
        "start_stop_cycles": 20,
        "forced_recovery_cases": 5,
        "device_disruption_cases": 5,
    }
    failures = [
        f"{metric}: {metrics[metric]} < {minimum}"
        for metric, minimum in thresholds.items()
        if metrics[metric] < minimum
    ]
    for metric in ("lost_recoverable_recordings", "unexplained_unhandled_crashes"):
        if metrics[metric]:
            failures.append(f"{metric}: {metrics[metric]} > 0")
    failures.extend(f"scenario not passed: {name}" for name, passed in scenarios.items() if not passed)
    return HardwareSoakReport(
        created_at=datetime.now(UTC).isoformat(),
        build=build_identity().to_dict(),
        metrics=metrics,
        scenarios=scenarios,
        thresholds=thresholds,
        failures=tuple(failures),
        passed=not failures,
    )


def write_hardware_soak_report(report: HardwareSoakReport, output: Path) -> Path:
    target = output.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return target
