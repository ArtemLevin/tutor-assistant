"""Application-layer orchestration contracts.

This package sits between UI adapters and domain/infrastructure services. It must
remain independent from PySide so workflows can be tested without a GUI runtime.
"""

from .audio_devices import (
    AudioDeviceInventory,
    AudioDeviceSelection,
    AudioInputDeviceSnapshot,
    RefreshAudioDevicesUseCase,
    SystemAudioSourceSnapshot,
)
from .audio_preflight import AudioPreflightResult, AudioPreflightUseCase
from .recording import (
    RecordingHealthSnapshot,
    RecordingLevelsSnapshot,
    RecordingRuntimeRecorder,
    RecordingRuntimeState,
    RecordingWorkflowController,
    RecordingWorkflowPhase,
    RecordingWorkflowRejected,
    StartedRecording,
    StartRecordingUseCase,
)
from .recording_health import (
    RecordingHealthAction,
    RecordingHealthAssessment,
    RecordingHealthMonitor,
    RecordingHealthPolicy,
    RecordingHealthSample,
    RecordingHealthSeverity,
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
    "AudioDeviceInventory",
    "AudioDeviceSelection",
    "AudioInputDeviceSnapshot",
    "AudioPreflightResult",
    "RefreshAudioDevicesUseCase",
    "SystemAudioSourceSnapshot",
    "AudioPreflightUseCase",
    "RecordingHealthAction",
    "RecordingHealthAssessment",
    "RecordingHealthMonitor",
    "RecordingHealthPolicy",
    "RecordingHealthSample",
    "RecordingHealthSeverity",
    "RecordingRecoveryOutcome",
    "RecordingHealthSnapshot",
    "RecordingLevelsSnapshot",
    "RecordingRecoveryState",
    "RecordingRuntimeRecorder",
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
