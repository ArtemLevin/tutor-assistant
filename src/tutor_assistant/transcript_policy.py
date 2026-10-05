from __future__ import annotations


def transcript_has_content(text: str) -> bool:
    """Return whether transcript text contains at least one letter or digit."""

    return any(character.isalnum() for character in text)
