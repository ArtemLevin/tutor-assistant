"""Qt-free admission policy for destructive managed-content cleanup."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BulkCleanupRuntime:
    recording_active: bool = False
    recording_stopping: bool = False
    transcription_active: bool = False
    publication_active: bool = False
    normalization_active: bool = False
    import_active: bool = False
    editor_dirty: bool = False
    background_active: bool = False
    shutdown_requested: bool = False


def cleanup_runtime_blockers(snapshot: BulkCleanupRuntime) -> tuple[str, ...]:
    """Block the entire operation; do not silently skip running lessons."""
    reasons: list[str] = []
    if snapshot.shutdown_requested:
        reasons.append("Приложение завершает работу")
    if snapshot.recording_active or snapshot.recording_stopping:
        reasons.append("Идёт запись или завершение записи")
    if snapshot.transcription_active:
        reasons.append("Выполняется транскрибация")
    if snapshot.publication_active:
        reasons.append("Выполняется публикация")
    if snapshot.normalization_active:
        reasons.append("Выполняется нормализация")
    if snapshot.import_active:
        reasons.append("Выполняется импорт")
    if snapshot.editor_dirty:
        reasons.append("Открыт редактор транскрипта с несохранёнными изменениями")
    if snapshot.background_active:
        reasons.append("Выполняются фоновые операции")
    return tuple(reasons)
