from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}: {count}\n{old}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


recording = Path("src/tutor_assistant/application/recording.py")
replace_once(
    recording,
    '''class RecordingRecorder(Protocol):
    """Capture port required by the start-recording use case."""

    @property
    def active(self) -> bool: ...

    def start(self, output_dir: Path, mic_device: int, system_source: object) -> None: ...

    def stop(self) -> object: ...


class RecordingPipeline(Protocol):
''',
    '''class RecordingRecorder(Protocol):
    """Capture port required by the start-recording use case."""

    @property
    def active(self) -> bool: ...

    def start(self, output_dir: Path, mic_device: int, system_source: object) -> None: ...

    def stop(self) -> object: ...


class RecordingLevelsSnapshot(Protocol):
    """Read-only audio levels exposed to presentation monitoring."""

    @property
    def microphone(self) -> float: ...

    @property
    def system(self) -> float: ...


class RecordingHealthSnapshot(Protocol):
    """Read-only recorder health exposed without infrastructure coupling."""

    @property
    def microphone_queue_percent(self) -> int: ...

    @property
    def system_queue_percent(self) -> int: ...

    @property
    def microphone_dropped_blocks(self) -> int: ...

    @property
    def system_dropped_blocks(self) -> int: ...

    @property
    def max_writer_latency_ms(self) -> float: ...

    @property
    def microphone_silence_seconds(self) -> float: ...

    @property
    def system_silence_seconds(self) -> float: ...

    @property
    def microphone_callback_age_seconds(self) -> float: ...

    @property
    def system_callback_age_seconds(self) -> float: ...

    @property
    def stream_errors(self) -> tuple[str, ...]: ...

    @property
    def reconnect_attempts(self) -> int: ...


class RecordingRuntimeRecorder(RecordingRecorder, Protocol):
    """Recorder view required by the production UI while capture is active."""

    @property
    def levels(self) -> RecordingLevelsSnapshot: ...

    @property
    def health(self) -> RecordingHealthSnapshot: ...


class RecordingPipeline(Protocol):
''',
)

application_init = Path("src/tutor_assistant/application/__init__.py")
replace_once(
    application_init,
    '''from .recording import (
    RecordingRuntimeState,
''',
    '''from .recording import (
    RecordingHealthSnapshot,
    RecordingLevelsSnapshot,
    RecordingRuntimeRecorder,
    RecordingRuntimeState,
''',
)
replace_once(
    application_init,
    '''    "RecordingRecoveryState",
    "RecordingRuntimeState",
''',
    '''    "RecordingHealthSnapshot",
    "RecordingLevelsSnapshot",
    "RecordingRecoveryState",
    "RecordingRuntimeRecorder",
    "RecordingRuntimeState",
''',
)

app = Path("src/tutor_assistant/ui/app.py")
replace_once(
    app,
    '''from ..config import AppConfig, load_students
''',
    '''from ..application import RecordingRuntimeRecorder
from ..config import AppConfig, load_students
''',
)
replace_once(app, '''    DualRecorder,\n''', '''''')
replace_once(
    app,
    '''        self.recorder: DualRecorder | None = None
''',
    '''        self.recorder: RecordingRuntimeRecorder | None = None
''',
)

publication = Path("src/tutor_assistant/ui/transcript_publication_app.py")
replace_once(publication, '''from ..recording import DualRecorder\n''', '''''')
replace_once(
    publication,
    '''        base_app.DualRecorder = self._create_configured_recorder
''',
    '''''',
)
replace_once(
    publication,
    '''    def _create_configured_recorder(self, *args, **kwargs) -> DualRecorder:
        kwargs["output_format"] = self.config.recording.output_format
        return DualRecorder(*args, **kwargs)

''',
    '''''',
)
replace_once(
    publication,
    '''        super().closeEvent(event)
        if event.isAccepted():
            base_app.DualRecorder = DualRecorder
''',
    '''        super().closeEvent(event)
''',
)

audio = Path("src/tutor_assistant/ui/audio_resilient_app.py")
replace_once(
    audio,
    '''import logging

from PySide6.QtWidgets import QMessageBox
''',
    '''import logging
from typing import cast

from PySide6.QtWidgets import QMessageBox
''',
)
replace_once(
    audio,
    '''from ..application.recording import (
    RecordingRuntimeState,
''',
    '''from ..application.recording import (
    RecordingRuntimeRecorder,
    RecordingRuntimeState,
''',
)
replace_once(
    audio,
    '''    def _create_live_recorder(self):
        return self._create_configured_recorder(
            self.config.recording.sample_rate,
            self.config.recording.channels,
            self.config.recording.chunk_seconds,
            self.config.recording.queue_blocks,
            self.config.recording.target_sample_rate,
        )
''',
    '''    def _create_live_recorder(self) -> RecordingRuntimeRecorder:
        return DualRecorder(
            self.config.recording.sample_rate,
            self.config.recording.channels,
            self.config.recording.chunk_seconds,
            self.config.recording.queue_blocks,
            self.config.recording.target_sample_rate,
            output_format=self.config.recording.output_format,
        )
''',
)
replace_once(
    audio,
    '''            self.recorder = started.recorder
''',
    '''            self.recorder = cast(RecordingRuntimeRecorder, started.recorder)
''',
)

test = Path("tests/test_recording_runtime_port_architecture.py")
if test.exists():
    raise RuntimeError(f"Refusing to overwrite existing {test}")
test.write_text(
    '''"""Architecture gates for runtime recorder ownership."""

from __future__ import annotations

import inspect
from pathlib import Path

from tutor_assistant.application.recording import (
    RecordingHealthSnapshot,
    RecordingLevelsSnapshot,
    RecordingRuntimeRecorder,
)
from tutor_assistant.ui import app as base_app
from tutor_assistant.ui.audio_resilient_app import MainWindow as ProductionAudioMainWindow


def test_application_runtime_recorder_port_is_qt_and_infrastructure_independent() -> None:
    application_source = Path("src/tutor_assistant/application/recording.py").read_text(
        encoding="utf-8"
    )
    levels = inspect.getsource(RecordingLevelsSnapshot)
    health = inspect.getsource(RecordingHealthSnapshot)
    runtime = inspect.getsource(RecordingRuntimeRecorder)

    assert "PySide6" not in application_source
    assert "DualRecorder" not in application_source
    assert "def microphone(self) -> float" in levels
    assert "def system(self) -> float" in levels
    assert "def stream_errors(self) -> tuple[str, ...]" in health
    assert "def reconnect_attempts(self) -> int" in health
    assert "RecordingRecorder" in runtime
    assert "def levels(self) -> RecordingLevelsSnapshot" in runtime
    assert "def health(self) -> RecordingHealthSnapshot" in runtime


def test_base_ui_has_no_concrete_dual_recorder_dependency() -> None:
    source = Path("src/tutor_assistant/ui/app.py").read_text(encoding="utf-8")

    assert "DualRecorder" not in source
    assert "RecordingRuntimeRecorder" in source
    assert "self.recorder: RecordingRuntimeRecorder | None = None" in source


def test_publication_adapter_no_longer_monkeypatches_base_recorder() -> None:
    source = Path("src/tutor_assistant/ui/transcript_publication_app.py").read_text(
        encoding="utf-8"
    )

    assert "DualRecorder" not in source
    assert "base_app.DualRecorder" not in source
    assert "_create_configured_recorder" not in source


def test_production_audio_adapter_owns_live_recorder_construction() -> None:
    method = inspect.getsource(ProductionAudioMainWindow._create_live_recorder)

    assert "DualRecorder(" in method
    assert "output_format=self.config.recording.output_format" in method
    assert "_create_configured_recorder" not in method


def test_base_tick_depends_on_runtime_snapshots_not_concrete_recorder() -> None:
    method = inspect.getsource(base_app.MainWindow._tick)

    assert "self.recorder.levels" in method
    assert "self.recorder.health" in method
    assert "DualRecorder" not in method
''',
    encoding="utf-8",
)

# Final architectural invariants before the workflow commits anything.
app_source = app.read_text(encoding="utf-8")
publication_source = publication.read_text(encoding="utf-8")
audio_source = audio.read_text(encoding="utf-8")
if "DualRecorder" in app_source:
    raise RuntimeError("Base UI still depends on DualRecorder")
if "DualRecorder" in publication_source:
    raise RuntimeError("Publication adapter still depends on DualRecorder")
if "base_app.DualRecorder" in publication_source:
    raise RuntimeError("Legacy recorder monkeypatch still exists")
if "output_format=self.config.recording.output_format" not in audio_source:
    raise RuntimeError("Production live recorder lost configured output format")
