from __future__ import annotations

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
