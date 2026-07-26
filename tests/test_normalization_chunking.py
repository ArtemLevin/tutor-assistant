from __future__ import annotations

from tutor_assistant.normalization.chunking import chunk_segments
from tutor_assistant.normalization.models import SourceSegment


def _segments(count: int) -> list[SourceSegment]:
    return [
        SourceSegment(
            source_segment_id=index,
            start=float(index),
            end=float(index) + 0.5,
            text=f"Сегмент {index}",
        )
        for index in range(1, count + 1)
    ]


def test_each_segment_is_target_exactly_once_with_context_overlap() -> None:
    chunks = chunk_segments(
        _segments(12),
        max_segments=5,
        max_characters=10_000,
        overlap_segments=2,
    )

    assert [item for chunk in chunks for item in chunk.target_ids] == list(range(1, 13))
    assert chunks[0].target_ids == (1, 2, 3, 4, 5)
    assert [item.source_segment_id for item in chunks[1].segments] == list(range(4, 13))
    assert [item.source_segment_id for item in chunks[1].segments if item.context_only] == [4, 5, 11, 12]


def test_character_limit_splits_targets_without_losing_large_segment() -> None:
    segments = [
        SourceSegment(source_segment_id=1, text="a" * 8),
        SourceSegment(source_segment_id=2, text="b" * 8),
        SourceSegment(source_segment_id=3, text="c" * 30),
    ]

    chunks = chunk_segments(
        segments,
        max_segments=10,
        max_characters=10,
        overlap_segments=0,
    )

    assert [chunk.target_ids for chunk in chunks] == [(1,), (2,), (3,)]


def test_segments_are_sorted_by_time_before_chunking() -> None:
    segments = [
        SourceSegment(source_segment_id=2, start=5, text="Позже"),
        SourceSegment(source_segment_id=1, start=1, text="Раньше"),
    ]

    chunks = chunk_segments(
        segments,
        max_segments=10,
        max_characters=100,
        overlap_segments=0,
    )

    assert chunks[0].target_ids == (1, 2)
