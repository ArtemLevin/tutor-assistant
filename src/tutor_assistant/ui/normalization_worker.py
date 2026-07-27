
from __future__ import annotations

import traceback

from PySide6.QtCore import QThread, Signal

from ..normalization.errors import NormalizationResumeConfirmationRequired
from ..normalization.service import NormalizationService


class NormalizationWorker(QThread):
    progress = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    resume_confirmation_required = Signal(object)

    def __init__(self, service: NormalizationService, **kwargs) -> None:
        super().__init__()
        self.service = service
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            result = self.service.normalize_lesson(
                **self.kwargs,
                progress=self.progress.emit,
            )
        except NormalizationResumeConfirmationRequired as exc:
            self.resume_confirmation_required.emit(exc)
        except Exception:
            self.failed.emit(traceback.format_exc())
        else:
            self.succeeded.emit(result)
