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
from .latex_monitor import (
    LatexMonitorCoordinator,
    LatexMonitorLifecycleState,
    LatexMonitorScanAction,
    LatexMonitorScanDecision,
    LatexMonitorScanTrigger,
)
from .normalization import (
    NormalizationAfterWorkerAction,
    NormalizationAutoAction,
    NormalizationAutoContext,
    NormalizationAutoDecision,
    NormalizationCoordinator,
    NormalizationLifecycleState,
    NormalizationManualStartContext,
    NormalizationProgressSnapshot,
    NormalizationStartBlock,
    NormalizationStartDecision,
)
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
from .shutdown import (
    ShutdownCloseAction,
    ShutdownCloseDecision,
    ShutdownCoordinator,
    ShutdownDrainAction,
    ShutdownDrainPlan,
    ShutdownPhase,
    ShutdownRuntimeSnapshot,
)
from .transcription_queue import (
    TranscriptionAudioMissingError,
    TranscriptionPumpContext,
    TranscriptionQueueCoordinator,
    TranscriptionQueueEntrySnapshot,
    TranscriptionQueueSnapshot,
    TranscriptionSubmission,
)
from .workspace import (
    LessonWorkspaceContext,
    WorkspaceContextCoordinator,
    WorkspaceContextSnapshot,
    WorkspaceStudentContext,
)

__all__ = [
    "AudioDeviceInventory",
    "AudioDeviceSelection",
    "AudioInputDeviceSnapshot",
    "AudioPreflightResult",
    "RefreshAudioDevicesUseCase",
    "SystemAudioSourceSnapshot",
    "AudioPreflightUseCase",
    "LatexMonitorCoordinator",
    "LatexMonitorLifecycleState",
    "LatexMonitorScanAction",
    "LatexMonitorScanDecision",
    "LatexMonitorScanTrigger",
    "LessonWorkspaceContext",
    "NormalizationAfterWorkerAction",
    "NormalizationAutoAction",
    "NormalizationAutoContext",
    "NormalizationAutoDecision",
    "NormalizationCoordinator",
    "NormalizationLifecycleState",
    "NormalizationManualStartContext",
    "NormalizationProgressSnapshot",
    "NormalizationStartBlock",
    "NormalizationStartDecision",
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
    "ShutdownCloseAction",
    "ShutdownCloseDecision",
    "ShutdownCoordinator",
    "ShutdownDrainAction",
    "ShutdownDrainPlan",
    "ShutdownPhase",
    "ShutdownRuntimeSnapshot",
    "StartedRecording",
    "StartRecordingUseCase",
    "StopRecordingUseCase",
    "TranscriptionAudioMissingError",
    "TranscriptionPumpContext",
    "TranscriptionQueueCoordinator",
    "TranscriptionQueueEntrySnapshot",
    "TranscriptionQueueSnapshot",
    "TranscriptionSubmission",
    "WorkspaceContextCoordinator",
    "WorkspaceContextSnapshot",
    "WorkspaceStudentContext",
]
