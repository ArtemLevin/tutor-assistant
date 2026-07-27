from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

replacements = {
    ROOT / "tests/test_normalization_models.py": {
        'assert PROMPT_VERSION == "transcript-normalizer.v2"':
            'assert PROMPT_VERSION == "educational-content-filter.v1"',
    },
    ROOT / "tests/test_normalization_service.py": {
        'assert "event=normalization_completed" in caplog.text':
            'assert "event=content_filter_completed" in caplog.text',
    },
}

for path, changes in replacements.items():
    text = path.read_text(encoding="utf-8")
    for old, new in changes.items():
        if old not in text:
            raise SystemExit(f"Expected fragment missing in {path}: {old}")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8", newline="\n")

Path(__file__).unlink()
