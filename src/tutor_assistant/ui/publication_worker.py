from __future__ import annotations

import queue
import traceback

from PySide6.QtCore import QThread, Signal

from ..application.publication_queue import PublicationSubmission
from ..pipeline import LessonPipeline


class PublicationWorker(QThread):
    """Qt transport adapter for sequential automatic transcript publication."""

    succeeded = Signal(str, object)
    failed = Signal(str, object, str)
    became_idle = Signal()

    def __init__(self, pipeline: LessonPipeline) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.pending: queue.Queue[PublicationSubmission | None] = queue.Queue()
        self.busy = False
        self._shutdown_sent = False

    def submit(self, submission: PublicationSubmission) -> None:
        if self._shutdown_sent:
            raise RuntimeError("Поток публикации завершает работу")
        self.pending.put(submission)

    def shutdown(self) -> None:
        if not self._shutdown_sent:
            self._shutdown_sent = True
            self.pending.put(None)

    def run(self) -> None:
        while True:
            submission = self.pending.get()
            if submission is None:
                self.pending.task_done()
                return
            self.busy = True
            try:
                result = self.pipeline.publish_automatic_transcript(
                    submission.lesson,
                    revision_number=submission.revision_number,
                    content_sha256=submission.content_sha256,
                    repository_path=submission.repository_path,
                )
                self.succeeded.emit(submission.job_id, result)
            except Exception as exc:
                self.failed.emit(
                    submission.job_id,
                    exc,
                    traceback.format_exc(),
                )
            finally:
                self.busy = False
                self.pending.task_done()
                self.became_idle.emit()
