from __future__ import annotations

from dataclasses import dataclass

from .models import SourceSegment


@dataclass(frozen=True, slots=True)
class NormalizationChunk:
    index: int
    segments: tuple[SourceSegment, ...]
    target_ids: tuple[int, ...]


def sort_segments(segments: list[SourceSegment]) -> list[SourceSegment]:
    indexed = list(enumerate(segments))
    indexed.sort(
        key=lambda item: (
            item[1].start is None,
            item[1].start if item[1].start is not None else float("inf"),
            item[1].end if item[1].end is not None else float("inf"),
            item[0],
        )
    )
    return [segment.model_copy(update={"context_only": False}) for _, segment in indexed]


def chunk_segments(
    segments: list[SourceSegment],
    *,
    max_segments: int,
    max_characters: int,
    overlap_segments: int,
) -> list[NormalizationChunk]:
    if not segments:
        raise ValueError("Для нормализации нужен хотя бы один сегмент")
    if max_segments <= 0 or max_characters <= 0:
        raise ValueError("Размер блока должен быть положительным")
    if overlap_segments < 0 or overlap_segments >= max_segments:
        raise ValueError("Некорректный context overlap")

    ordered = sort_segments(segments)
    target_ranges: list[tuple[int, int]] = []
    start = 0
    while start < len(ordered):
        end = start
        characters = 0
        while end < len(ordered) and end - start < max_segments:
            addition = len(ordered[end].text)
            if end > start and characters + addition > max_characters:
                break
            characters += addition
            end += 1
        if end == start:
            end += 1
        target_ranges.append((start, end))
        start = end

    chunks: list[NormalizationChunk] = []
    for index, (target_start, target_end) in enumerate(target_ranges):
        context_start = max(0, target_start - overlap_segments)
        context_end = min(len(ordered), target_end + overlap_segments)
        chunk_items = []
        for position in range(context_start, context_end):
            chunk_items.append(
                ordered[position].model_copy(
                    update={"context_only": not target_start <= position < target_end}
                )
            )
        targets = tuple(ordered[position].source_segment_id for position in range(target_start, target_end))
        chunks.append(NormalizationChunk(index=index, segments=tuple(chunk_items), target_ids=targets))

    classified = [source_id for chunk in chunks for source_id in chunk.target_ids]
    expected = [segment.source_segment_id for segment in ordered]
    if classified != expected:
        raise RuntimeError("Каждый исходный сегмент должен быть целевым ровно один раз")
    return chunks
