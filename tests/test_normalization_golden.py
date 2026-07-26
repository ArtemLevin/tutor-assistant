from __future__ import annotations

import json
from pathlib import Path


def test_golden_dataset_is_anonymized_and_protects_critical_content() -> None:
    path = Path(__file__).parent / "golden" / "transcript_normalization.json"
    items = json.loads(path.read_text(encoding="utf-8"))

    assert {item["subject"] for item in items} >= {
        "mathematics",
        "physics",
        "chemistry",
    }
    assert len({item["id"] for item in items}) == len(items)
    protected = [item for item in items if item["blocking_drop"]]
    assert protected
    assert all("drop" not in item["allowed_actions"] for item in protected)
    assert not any("Артём" in item["text"] for item in items)
