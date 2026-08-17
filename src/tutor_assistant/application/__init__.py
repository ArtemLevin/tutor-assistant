"""Application-layer orchestration contracts.

This package sits between UI adapters and domain/infrastructure services. It must
remain independent from PySide so workflows can be tested without a GUI runtime.
"""

from .audio_preflight import AudioPreflightResult, AudioPreflightUseCase
from .recording import (
    RecordingRuntimeState,
    RecordingWorkflowController,
    RecordingWorkflowPhase,
    RecordingWorkflowRejected,
    StartedRecording,
    StartRecordingUseCase,
)
from .recording_recovery import (
    RecordingRecoveryOutcome,
    RecordingRecoveryState,
    RecoverRecordingUseCase,
)
from .recording_stop import (
    RecordingStopOutcome,
    RecordingStopSession,
    RecordingStopState,
    StopRecordingUseCase,
)

__all__ = [
    "AudioPreflightResult",
    "AudioPreflightUseCase",
    "RecordingRecoveryOutcome",
    "RecordingRecoveryState",
    "RecordingRuntimeState",
    "RecordingStopOutcome",
    "RecordingStopSession",
    "RecordingStopState",
    "RecordingWorkflowController",
    "RecordingWorkflowPhase",
    "RecordingWorkflowRejected",
    "RecoverRecordingUseCase",
    "StartedRecording",
    "StartRecordingUseCase",
    "StopRecordingUseCase",
]
