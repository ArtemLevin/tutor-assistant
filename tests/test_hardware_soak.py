from __future__ import annotations

import json
from pathlib import Path

from tutor_assistant.hardware_soak import (
    REQUIRED_SCENARIOS,
    collect_hardware_observations,
    evaluate_hardware_soak,
    write_hardware_soak_report,
)


def accepted_evidence() -> dict[str, object]:
    return {
        "metrics": {
            "cumulative_recording_seconds": 20 * 3600,
            "longest_recording_seconds": 2 * 3600,
            "start_stop_cycles": 20,
            "forced_recovery_cases": 5,
            "device_disruption_cases": 5,
            "lost_recoverable_recordings": 0,
            "unexplained_unhandled_crashes": 0,
        },
        "scenarios": dict.fromkeys(REQUIRED_SCENARIOS, True),
    }


def test_hardware_acceptance_requires_real_observations() -> None:
    report = evaluate_hardware_soak({})

    assert not report.passed
    assert any("cumulative_recording_seconds" in item for item in report.failures)
    assert any("scenario not passed" in item for item in report.failures)


def test_complete_hardware_evidence_passes_without_private_payload(tmp_path: Path) -> None:
    evidence = accepted_evidence()
    evidence["student_name"] = "Private Student"
    evidence["transcript"] = "Private lesson transcript"

    report = evaluate_hardware_soak(evidence)
    output = write_hardware_soak_report(report, tmp_path / "hardware.json")

    assert report.passed
    serialized = output.read_text(encoding="utf-8")
    assert "Private Student" not in serialized
    assert "Private lesson transcript" not in serialized


def test_lost_recoverable_recording_blocks_stable_release() -> None:
    evidence = accepted_evidence()
    evidence["metrics"]["lost_recoverable_recordings"] = 1  # type: ignore[index]

    report = evaluate_hardware_soak(evidence)

    assert not report.passed
    assert any("lost_recoverable_recordings" in item for item in report.failures)


def test_workspace_evidence_hashes_device_identity_without_audio_or_names(tmp_path: Path) -> None:
    recording = tmp_path / "lessons" / "private-student" / "recording"
    recording.mkdir(parents=True)
    (recording / "session.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "duration_seconds": 120,
                "system_device_id": "private-device-identifier",
                "microphone_chunks": 4,
                "system_chunks": 5,
                "microphone_dropped_blocks": 1,
            }
        ),
        encoding="utf-8",
    )

    observations = collect_hardware_observations(tmp_path)

    assert observations["metrics"]["cumulative_recording_seconds"] == 120  # type: ignore[index]
    assert observations["metrics"]["captured_blocks"] == 9  # type: ignore[index]
    assert "private-device-identifier" not in json.dumps(observations)
    assert "private-student" not in json.dumps(observations)
