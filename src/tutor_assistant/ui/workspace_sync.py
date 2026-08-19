from __future__ import annotations

from typing import Any

from ..application import WorkspaceContextCoordinator, WorkspaceContextSnapshot
from .parallel_review import ParallelReviewPolicy, parallel_context_text


class WorkspaceSyncMixin:
    """Production adapter that synchronizes recording, review and cockpit state."""

    def _workspace_coordinator(self) -> WorkspaceContextCoordinator:
        coordinator = self.__dict__.get("_workspace_context_coordinator")
        if coordinator is None:
            coordinator = WorkspaceContextCoordinator()
            self.__dict__["_workspace_context_coordinator"] = coordinator
        return coordinator

    def workspace_context_snapshot(self) -> WorkspaceContextSnapshot:
        recorder = getattr(self, "recorder", None)
        return self._workspace_coordinator().sync(
            recording_lesson=getattr(self, "recording_lesson", None),
            review_lesson=getattr(self, "lesson", None),
            recording_active=bool(recorder and recorder.active),
            recording_stopping=bool(getattr(self, "_recording_stop_started", False)),
            elapsed_seconds=int(getattr(self, "recording_seconds", 0)),
        )

    def _parallel_policy(self) -> ParallelReviewPolicy:
        return ParallelReviewPolicy.from_workspace(self.workspace_context_snapshot())

    def _render_workspace_snapshot(self, workspace: WorkspaceContextSnapshot) -> None:
        if not hasattr(self, "header_stop_button"):
            return
        text = parallel_context_text(workspace)
        self.parallel_context_label.setText(text)
        self.parallel_context_label.setVisible(bool(text))
        self.header_stop_button.setVisible(workspace.recording_busy)
        self.header_stop_button.setEnabled(
            workspace.recording_active and not workspace.recording_stopping
        )
        self.header_stop_button.setText(
            "Сохраняю запись…"
            if workspace.recording_stopping
            else "■ Завершить запись"
        )
        self.play_segment_button.setEnabled(
            workspace.audio_playback_allowed and workspace.review is not None
        )

    def _sync_parallel_review_ui(self) -> None:
        self._render_workspace_snapshot(self.workspace_context_snapshot())

    def _workspace_state_changed(self) -> WorkspaceContextSnapshot:
        workspace = self.workspace_context_snapshot()
        self._render_workspace_snapshot(workspace)
        cockpit = getattr(self, "teacher_cockpit", None)
        if cockpit is not None:
            cockpit.refresh(workspace=workspace)
        return workspace

    def _refresh_teacher_cockpit(self) -> None:
        self._workspace_state_changed()

    def _load_review_lesson(
        self,
        lesson,
        *,
        restore_form: bool | None = None,
    ) -> None:
        super()._load_review_lesson(lesson, restore_form=restore_form)
        self._workspace_state_changed()

    def _forget_trashed_lesson(self, lesson_id: str) -> None:
        super()._forget_trashed_lesson(lesson_id)
        self._workspace_coordinator().invalidate_review(lesson_id)
        self._workspace_state_changed()

    def _normalization_applied(self, lesson_id: str, text: str) -> None:
        super()._normalization_applied(lesson_id, text)
        self._workspace_state_changed()

    def _crm_students_changed(self) -> None:
        super()._crm_students_changed()
        self._workspace_state_changed()

    def _worker_finished(self, worker: Any) -> None:
        super()._worker_finished(worker)
        self._workspace_state_changed()
