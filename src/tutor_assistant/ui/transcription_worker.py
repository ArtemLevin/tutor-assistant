from __future__ import annotations

import queue
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..domain import Lesson
from ..pipeline import LessonPipeline


class TranscriptionWorker(QThread):
    """Qt transport adapter for sequential background transcription execution."""

    succeeded = Signal(str, object)
    failed = Signal(str, str)
    became_idle = Signal()

    def __init__(self, pipeline: LessonPipeline) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.pending: queue.Queue[tuple[str, Lesson, Path] | None] = queue.Queue()
        self.busy = False
        self._shutdown_sent = False

    def submit(self, job_id: str, lesson: Lesson, audio: Path) -> None:
        if self._shutdown_sent:
            raise RuntimeError("Поток транскрибации завершает работу")
        self.pending.put((job_id, lesson, audio))

    def shutdown(self) -> None:
        if not self._shutdown_sent:
            self._shutdown_sent = True
            self.pending.put(None)

    def run(self) -> None:
        while True:
            item = self.pending.get()
            if item is None:
                self.pending.task_done()
                return
            job_id, lesson, audio = item
            self.busy = True
            try:
                self.succeeded.emit(job_id, self.pipeline.transcribe(lesson, audio))
            except Exception:
                self.failed.emit(job_id, traceback.format_exc())
            finally:
                self.busy = False
                self.pending.task_done()
                self.became_idle.emit()
