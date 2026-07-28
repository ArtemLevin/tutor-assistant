from __future__ import annotations

from PySide6.QtWidgets import QApplication

from tutor_assistant.normalization.errors import NormalizationResumeConfirmationRequired
from tutor_assistant.normalization.models import NormalizationProgress
from tutor_assistant.ui.normalization_worker import NormalizationWorker

_APPLICATION: QApplication | None = None


def _application() -> QApplication:
    global _APPLICATION
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        _APPLICATION = existing
    elif _APPLICATION is None:
        _APPLICATION = QApplication([])
    return _APPLICATION


def test_normalization_worker_forwards_progress_and_result() -> None:
    _application()

    class Service:
        def normalize_lesson(self, **kwargs):
            kwargs["progress"](
                NormalizationProgress(
                    run_id=1,
                    current_chunk=1,
                    total_chunks=3,
                    completed_chunks=2,
                    reused_chunks=1,
                    provider_requests=1,
                    state="completed",
                )
            )
            return "result"

    progress = []
    results = []
    worker = NormalizationWorker(Service(), lesson_id="lesson")
    worker.progress.connect(progress.append)
    worker.succeeded.connect(results.append)

    worker.run()

    assert progress[0].completed_chunks == 2
    assert progress[0].reused_chunks == 1
    assert results == ["result"]


def test_normalization_worker_surfaces_resume_confirmation() -> None:
    _application()

    class Service:
        def normalize_lesson(self, **_kwargs):
            raise NormalizationResumeConfirmationRequired(4, (1, 3))

    confirmations = []
    worker = NormalizationWorker(Service(), lesson_id="lesson")
    worker.resume_confirmation_required.connect(confirmations.append)

    worker.run()

    assert confirmations[0].run_id == 4
    assert confirmations[0].chunk_indices == (1, 3)
