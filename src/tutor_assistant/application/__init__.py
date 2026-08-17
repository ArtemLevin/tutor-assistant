"""Application-layer orchestration contracts.

This package sits between UI adapters and domain/infrastructure services. It must
remain independent from PySide so workflows can be tested without a GUI runtime.
"""

from .recording import (
    RecordingRuntimeState,
    RecordingWorkflowController,
    RecordingWorkflowPhase,
    RecordingWorkflowRejected,
    StartedRecording,
    StartRecordingUseCase,
)
from .recording_stop import (
    RecordingStopOutcome,
    RecordingStopSession,
    RecordingStopState,
    StopRecordingUseCase,
)

__all__ = [
    "RecordingRuntimeState",
    "RecordingStopOutcome",
    "RecordingStopSession",
    "RecordingStopState",
    "RecordingWorkflowController",
    "RecordingWorkflowPhase",
    "RecordingWorkflowRejected",
    "StartedRecording",
    "StartRecordingUseCase",
    "StopRecordingUseCase",
]
