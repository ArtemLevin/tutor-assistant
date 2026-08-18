from __future__ import annotations

from dataclasses import dataclass

from ..application.transcription_queue import TranscriptionQueueSnapshot


@dataclass(frozen=True, slots=True)
class TranscriptionQueueRowPresentation:
    job_id: str
    text: str
    tooltip: str | None


@dataclass(frozen=True, slots=True)
class TranscriptionQueuePresentation:
    rows: tuple[TranscriptionQueueRowPresentation, ...]
    summary_text: str
    badge_text: str
    badge_tooltip: str


_STATUS_LABELS = {
    "waiting": "Ожидает",
    "running": "Транскрибируется",
    "ready": "Готов к проверке",
    "failed": "Ошибка",
}


def build_transcription_queue_presentation(
    snapshot: TranscriptionQueueSnapshot,
) -> TranscriptionQueuePresentation:
    rows = tuple(
        TranscriptionQueueRowPresentation(
            job_id=entry.job_id,
            text=(
                f"{_STATUS_LABELS.get(entry.status, entry.status)}  ·  "
                f"{entry.student_name}  ·  {entry.topic}"
            ),
            tooltip=entry.error[-1500:] if entry.error else None,
        )
        for entry in reversed(snapshot.entries)
    )
    return TranscriptionQueuePresentation(
        rows=rows,
        summary_text=(
            f"В обработке: {snapshot.unfinished_count} · "
            f"готовы к проверке: {snapshot.ready_count}"
        ),
        badge_text=f"≡ {snapshot.visible_count}",
        badge_tooltip=(
            f"В обработке: {snapshot.unfinished_count}\n"
            f"Готовы к проверке: {snapshot.ready_count}\n"
            "Нажмите, чтобы открыть очередь"
        ),
    )
