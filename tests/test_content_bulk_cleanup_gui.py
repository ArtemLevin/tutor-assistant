from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tutor_assistant.content import (  # noqa: E402
    BulkCleanupPreview,
    BulkCleanupProgress,
    BulkCleanupResult,
)
from tutor_assistant.ui.app import MainWindow  # noqa: E402
from tutor_assistant.ui.content_bulk_cleanup import BulkCleanupDialog  # noqa: E402


def test_cleanup_dialog_requires_destructive_confirmation_and_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = QApplication.instance() or QApplication([])
    preview = BulkCleanupPreview(
        fingerprint="a" * 64,
        workspace=tmp_path,
        active_lessons_count=2,
        trashed_lessons_count=1,
        lessons_bytes=1000,
        trash_bytes=200,
    )
    dialog = BulkCleanupDialog(preview)
    requested: list[str] = []
    dialog.confirmed.connect(requested.append)
    dialog.open()
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.Cancel
    )
    dialog.confirm_button.click()
    assert requested == []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.Yes
    )
    dialog.confirm_button.click()
    assert requested == [preview.fingerprint]

    dialog.set_running()
    assert not dialog.close_button.isEnabled()
    dialog.reject()
    assert dialog.isVisible()
    dialog.update_progress(BulkCleanupProgress(
        processed=2, total=3, purged=1, lesson_id="test-lesson"
    ))
    assert dialog.progress.value() == 2
    dialog.show_result(BulkCleanupResult(
        run_id="test", state="partial", total=3, purged=1, remaining_count=2
    ))
    assert dialog.close_button.isEnabled()
    assert "partial" in dialog.state.text()
    dialog.close()
    application.processEvents()


def test_cleanup_dialog_is_blocked_when_archive_is_busy(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    preview = BulkCleanupPreview(
        fingerprint="b" * 64,
        workspace=tmp_path,
        active_lessons_count=1,
        blockers=["Идёт публикация"],
    )
    dialog = BulkCleanupDialog(preview)
    assert not dialog.confirm_button.isEnabled()
    assert "Идёт публикация" in dialog.state.text()
    dialog.close()
    application.processEvents()



def test_cleanup_disables_direct_transcript_draft_writer(tmp_path: Path) -> None:
    draft = tmp_path / "lessons" / "already-removed" / "transcript" / "transcript_draft.json"
    state = SimpleNamespace(_cleanup_in_progress=True, _draft_path=lambda: draft)

    MainWindow._save_transcript_draft(state)

    assert not draft.exists()
    assert not draft.parent.exists()



def test_cleanup_dialog_allows_old_temporary_files_without_lessons(tmp_path: Path) -> None:
    application = QApplication.instance() or QApplication([])
    preview = BulkCleanupPreview(
        fingerprint="c" * 64, workspace=tmp_path,
        active_lessons_count=0, trashed_lessons_count=0,
        temporary_bytes=1024,
    )
    dialog = BulkCleanupDialog(preview)
    assert dialog.confirm_button.isEnabled()
    assert "Временные файлы" in dialog.summary.text()
    dialog.close()
    application.processEvents()



def test_trashing_current_lesson_clears_stale_transcript_state() -> None:
    class FakeTimer:
        active = True

        def stop(self) -> None:
            self.active = False

        def isActive(self) -> bool:
            return self.active

    cleared: list[str] = []
    disabled: list[bool] = []
    discarded: list[str] = []
    timer = FakeTimer()
    state = SimpleNamespace(
        lesson=SimpleNamespace(lesson_id="trashed-lesson"),
        _summary_dirty=True,
        draft_timer=timer,
        audio_path=SimpleNamespace(clear=lambda: cleared.append("audio")),
        transcript=SimpleNamespace(clear=lambda: cleared.append("transcript")),
        segment_table=SimpleNamespace(setRowCount=lambda count: cleared.append(f"rows:{count}")),
        approve=SimpleNamespace(setEnabled=disabled.append),
        publish_button=SimpleNamespace(setEnabled=disabled.append),
        transcription_queue_coordinator=SimpleNamespace(discard=discarded.append),
        _update_transcription_queue_ui=lambda: None,
    )
    state._clear_removed_lesson_view = lambda: MainWindow._clear_removed_lesson_view(state)

    MainWindow._forget_trashed_lesson(state, "trashed-lesson")

    assert state.lesson is None
    assert state._summary_dirty is False
    assert timer.isActive() is False
    assert cleared == ["audio", "transcript", "rows:0"]
    assert disabled == [False, False]
    assert discarded == ["trashed-lesson"]


def test_bulk_cleanup_does_not_treat_orphan_editor_flag_as_active_work() -> None:
    empty_queue = SimpleNamespace(entries=[])
    page = SimpleNamespace(
        _transcript_editing=False,
        _draft_running=False,
        import_cancellation=None,
    )
    state = SimpleNamespace(
        student_content_page=page,
        recorder=None,
        quick_countdown_timer=SimpleNamespace(isActive=lambda: False),
        _quick_start_pending=False,
        _recording_stop_started=False,
        _recording_lease=None,
        recording_lesson=None,
        transcription_worker=SimpleNamespace(
            busy=False, pending=SimpleNamespace(empty=lambda: True)
        ),
        transcription_queue_coordinator=SimpleNamespace(snapshot=lambda: empty_queue),
        publication_worker=SimpleNamespace(
            busy=False, pending=SimpleNamespace(empty=lambda: True)
        ),
        publication_queue_coordinator=SimpleNamespace(
            snapshot=lambda: empty_queue, active=None
        ),
        _normalization_cancellation=None,
        normalization_coordinator=SimpleNamespace(active=False),
        lesson=None,
        _summary_dirty=True,
        draft_timer=SimpleNamespace(isActive=lambda: True),
        workers=[],
        _shutdown_requested=False,
    )

    assert MainWindow._bulk_cleanup_runtime_blockers(state) == ()

    # Real, still-open unsaved changes remain a hard safety blocker.
    state.lesson = SimpleNamespace(lesson_id="active-editor")
    assert "Открыт редактор транскрипта с несохранёнными изменениями" in (
        MainWindow._bulk_cleanup_runtime_blockers(state)
    )



def test_completed_cleanup_reports_ui_refresh_failure_without_stuck_dialog() -> None:
    reported: list[BulkCleanupResult] = []
    statuses: list[tuple[str, str]] = []
    result = BulkCleanupResult(run_id="finished", state="completed", total=1, purged=1)
    state = SimpleNamespace(
        content_service=SimpleNamespace(
            repository=SimpleNamespace(
                bulk_cleanup_snapshot_rows=lambda: (_ for _ in ()).throw(
                    sqlite3.OperationalError("database temporarily locked")
                ),
            )
        ),
        _cleanup_dialog=SimpleNamespace(show_result=reported.append),
        _set_status=lambda message, tone: statuses.append((message, tone)),
    )

    MainWindow._bulk_cleanup_ready(state, result)

    assert reported == [result]
    assert result.state == "completed"
    assert len(result.warnings) == 1
    assert "OperationalError" in result.warnings[0]
    assert statuses and statuses[0][1] == "warning"
