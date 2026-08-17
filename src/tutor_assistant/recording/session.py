from __future__ import annotations

from enum import StrEnum
from typing import Any


class RecordingStatus(StrEnum):
    RECORDING = "recording"
    RECORDED = "recorded"
    FAILED_TO_START = "failed_to_start"
    FAILED_TO_STOP = "failed_to_stop"
    ENCODING_FAILED = "encoding_failed"
    COMPLETED = "completed"


class InvalidRecordingStatus(ValueError):
    pass


class InvalidRecordingTransition(ValueError):
    pass


ALLOWED_RECORDING_TRANSITIONS: dict[RecordingStatus, frozenset[RecordingStatus]] = {
    RecordingStatus.RECORDING: frozenset(
        {
            RecordingStatus.RECORDED,
            RecordingStatus.FAILED_TO_START,
            RecordingStatus.FAILED_TO_STOP,
            RecordingStatus.COMPLETED,
        }
    ),
    RecordingStatus.RECORDED: frozenset(
        {
            RecordingStatus.COMPLETED,
            RecordingStatus.FAILED_TO_STOP,
        }
    ),
    RecordingStatus.FAILED_TO_START: frozenset({RecordingStatus.COMPLETED}),
    RecordingStatus.FAILED_TO_STOP: frozenset({RecordingStatus.COMPLETED}),
    RecordingStatus.ENCODING_FAILED: frozenset({RecordingStatus.COMPLETED}),
    RecordingStatus.COMPLETED: frozenset({RecordingStatus.ENCODING_FAILED}),
}

RECOVERABLE_RECORDING_STATUSES = frozenset(
    {
        RecordingStatus.RECORDING,
        RecordingStatus.RECORDED,
        RecordingStatus.FAILED_TO_START,
        RecordingStatus.FAILED_TO_STOP,
        RecordingStatus.ENCODING_FAILED,
        RecordingStatus.COMPLETED,
    }
)


def recording_status(value: object) -> RecordingStatus | None:
    if value is None or value == "":
        return None
    try:
        return RecordingStatus(str(value))
    except ValueError as exc:
        raise InvalidRecordingStatus(f"Неизвестный статус записи: {value}") from exc


def is_recoverable_recording_status(value: object) -> bool:
    """Return whether a legacy/current session may safely be rebuilt from WAV chunks."""

    status = recording_status(value)
    return status is None or status in RECOVERABLE_RECORDING_STATUSES


def transition_recording_status(
    session: dict[str, Any],
    target: RecordingStatus,
    *,
    allow_legacy_source: bool = False,
) -> RecordingStatus:
    """Validate and apply a recording status transition in-place.

    Missing status is accepted only at explicit recovery/finalization boundaries so
    old or partially damaged manifests remain recoverable. Re-applying the current
    status is intentionally idempotent.
    """

    source = recording_status(session.get("status"))
    if source is None:
        if not allow_legacy_source:
            raise InvalidRecordingTransition(
                f"Нельзя перейти к {target.value}: исходный статус записи отсутствует"
            )
    elif source != target and target not in ALLOWED_RECORDING_TRANSITIONS[source]:
        raise InvalidRecordingTransition(
            f"Недопустимый переход записи: {source.value} → {target.value}"
        )
    session["status"] = target.value
    return target
