import ast
from pathlib import Path

from tutor_assistant.transcript_editing import select_verified_text


def test_edited_summary_is_canonical_on_approval() -> None:
    assert select_verified_text(
        ["Исходный сегмент"],
        "Исправленная формула: x² = 4",
        True,
    ) == "Исправленная формула: x² = 4"


def test_segments_are_used_when_summary_was_not_edited() -> None:
    assert select_verified_text(
        ["Первый", "второй"],
        "Старый сводный текст",
        False,
    ) == "Первый второй"


def test_main_window_does_not_reference_removed_prepare_next_lesson() -> None:
    source = Path("src/tutor_assistant/ui/app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    stale_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_prepare_next_lesson"
    ]
    assert stale_calls == []
