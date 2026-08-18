from __future__ import annotations

import inspect
from pathlib import Path

from tutor_assistant.ui import app as base_app
from tutor_assistant.ui import concurrent_app


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_application_queue_coordinator_is_qt_free() -> None:
    source = _source("src/tutor_assistant/application/transcription_queue.py")

    assert "PySide6" not in source
    assert "tutor_assistant.ui" not in source
    assert "from ..ui" not in source


def test_queue_presentation_is_qt_free() -> None:
    source = _source("src/tutor_assistant/ui/transcription_queue_presentation.py")

    assert "PySide6" not in source
    assert "QListWidget" not in source
    assert "QMessageBox" not in source


def test_transcription_worker_is_extracted_from_base_window_module() -> None:
    app_source = _source("src/tutor_assistant/ui/app.py")
    worker_source = _source("src/tutor_assistant/ui/transcription_worker.py")

    assert "class TranscriptionWorker" not in app_source
    assert "from .transcription_worker import TranscriptionWorker" in app_source
    assert "class TranscriptionWorker(QThread):" in worker_source


def test_base_ui_does_not_mutate_raw_queue_state_directly() -> None:
    source = _source("src/tutor_assistant/ui/app.py")

    assert "from ..transcription_queue import" not in source
    assert "TranscriptionQueue(" not in source
    assert "self.transcription_queue.start_next(" not in source
    assert "self.transcription_queue.complete(" not in source
    assert "self.transcription_queue.fail(" not in source
    assert "self.transcription_queue.restore(" not in source
    assert "self.transcription_queue.retry(" not in source
    assert "job.lesson.transition(JobStatus.RECORDED" not in source


def test_restore_and_presentation_delegate_to_pure_boundaries() -> None:
    restore_source = inspect.getsource(base_app.MainWindow._restore_background_jobs)
    presentation_source = inspect.getsource(base_app.MainWindow._update_transcription_queue_ui)

    assert "restore_history(" in restore_source
    assert "QueueStatus(" not in restore_source
    assert "for stored in" not in restore_source
    assert "build_transcription_queue_presentation(" in presentation_source
    assert "QueueStatus." not in presentation_source
    assert "ready = sum(" not in presentation_source


def test_retry_orchestration_is_shared_and_not_duplicated_in_concurrent_layer() -> None:
    base_retry = inspect.getsource(base_app.MainWindow._retry_transcription_job)
    concurrent_open = inspect.getsource(concurrent_app.MainWindow._open_processing_item)

    assert "transcription_queue_coordinator.retry(" in base_retry
    assert "job.lesson.transition(" not in concurrent_open
    assert "pipeline.save_state(" not in concurrent_open
    assert "transcription_queue.retry(" not in concurrent_open
    assert "_retry_transcription_job(job.id)" in concurrent_open
