from __future__ import annotations


def select_verified_text(segment_texts: list[str], summary_text: str, summary_dirty: bool) -> str:
    summary = summary_text.strip()
    if summary_dirty and summary:
        return summary
    segments = " ".join(segment_texts).strip()
    return segments or summary
