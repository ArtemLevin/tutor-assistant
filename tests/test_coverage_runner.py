from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("calls", "expected_returncode", "expected_branch_coverage"),
    [
        ("assert decide(True) == 1\n    assert decide(False) == 2", 0, "100.0%"),
        ("assert decide(True) == 1", 1, "50.0%"),
    ],
)
def test_coverage_runner_counts_and_enforces_real_conditional_branches(
    tmp_path: Path,
    calls: str,
    expected_returncode: int,
    expected_branch_coverage: str,
) -> None:
    source = tmp_path / "sample_source"
    source.mkdir()
    (source / "sample.py").write_text(
        "def decide(value):\n    if value:\n        return 1\n    return 2\n",
        encoding="utf-8",
    )
    suite = tmp_path / "sample_test.py"
    suite.write_text(
        "from sample import decide\n\ndef test_decision():\n    " + calls + "\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(source), environment.get("PYTHONPATH")) if item
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "test_coverage.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source",
            str(source),
            "--branch-fail-under",
            "100",
            "--",
            "-q",
            str(suite),
        ],
        capture_output=True,
        text=True,
        env=environment,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == expected_returncode, result.stdout + result.stderr
    assert expected_branch_coverage in result.stdout
    assert ("below 100.0%" in result.stdout) is bool(expected_returncode)


def test_required_production_gate_publishes_line_and_branch_coverage_artifact() -> None:
    root = Path(__file__).resolve().parents[1]
    gate = (root / ".github" / "workflows" / "release-gate.yml").read_text(encoding="utf-8")
    makefile = (root / "Makefile").read_text(encoding="utf-8")

    assert "scripts/test_coverage.py" in gate
    assert "production-py312-coverage.json" in gate
    assert "--fail-under 70 --branch-fail-under 45" in gate
    assert "--fail-under 70 --branch-fail-under 45" in makefile
    assert "coverage:" in makefile
