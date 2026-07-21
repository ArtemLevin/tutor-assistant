from tutor_assistant.transcript_editing import select_verified_text


def test_edited_summary_is_canonical_on_approval() -> None:
    assert (
        select_verified_text(
            ["Исходный сегмент"],
            "Исправленная формула: x² = 4",
            True,
        )
        == "Исправленная формула: x² = 4"
    )


def test_segments_are_used_when_summary_was_not_edited() -> None:
    assert (
        select_verified_text(
            ["Первый", "второй"],
            "Старый сводный текст",
            False,
        )
        == "Первый второй"
    )
