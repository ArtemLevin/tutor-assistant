from pathlib import Path

import numpy as np
import pytest

from tutor_assistant.recording.recorder import QueuedChunkWriter


def test_writer_thread_persists_blocks_outside_callback(tmp_path: Path) -> None:
    sf = pytest.importorskip("soundfile")
    chunks = []
    levels = []
    writer = QueuedChunkWriter(
        tmp_path, "mic", 8000, 1, 1, 8,
        lambda: chunks.append(True), levels.append,
    )
    writer.enqueue(np.full((4000, 1), 0.1, dtype="float32"), 1.0)
    writer.enqueue(np.full((4000, 1), 0.1, dtype="float32"), 2.0)
    writer.stop()
    files = sorted(tmp_path.glob("*.wav"))
    assert sum(sf.info(path).frames for path in files) == 8000
    assert levels
    assert writer.dropped_blocks == 0
