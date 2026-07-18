from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QLabel, QListWidgetItem, QMessageBox, QPushButton

from ..domain import JobStatus, Lesson
from . import app as base_app
from .parallel_review import (
    ParallelReviewPolicy,
    ProcessingAction,
    parallel_context_text,
    processing_action,
)
from .theme import set_button_kind


class MainWindow(base_app.MainWindow):
    """Main window with independent recording and transcript-review contexts."""

    def __init__(self, config_path: Path) -> None:
        super().__init__(config_path)
        self.parallel_context_label = QLabel()
        self.parallel_context_label.setObjectName("muted")
        self.parallel_context_label.setWordWrap(True)
        self.parallel_context_label.setVisible(False)
        self.header_layout.addWidget(self.parallel_context_label, 0, Qt.AlignVCenter)

        self.header_stop_button = set_button_kind(
            QPushButton("■ Завершить запись"), "danger"
        )
        self.header_stop_button.setToolTip(
            "Завершить текущую запись, "
            "не закрывая проверяемый транскрипт"
        )
        self.header_stop_button.clicked.connect(self.stop_recording)
        self.header_stop_button.setVisible(False)
        self.header_layout.addWidget(self.header_stop_button, 0, Qt.AlignVCenter)
        self._sync_parallel_review_ui()

    @property
    def review_lesson(self) -> Lesson | None:
        """Explicit name for the lesson currently opened in the review/publish UI."""

        return self.lesson

    @review_lesson.setter
    def review_lesson(self, lesson: Lesson | None) -> None:
        self.lesson = lesson

    def _parallel_policy(self) -> ParallelReviewPolicy:
        return ParallelReviewPolicy(
            recording_active=bool(self.recorder and self.recorder.active),
            recording_stopping=self._recording_stop_started,
        )

    def _sync_parallel_review_ui(self) -> None:
        if not hasattr(self, "header_stop_button"):
            return
        policy = self._parallel_policy()
        recording = self.recording_lesson if policy.recording_busy else None
        review = self.review_lesson
        text = parallel_context_text(
            recording_student=recording.student.full_name if recording else None,
            recording_topic=recording.topic if recording else None,
            review_student=review.student.full_name if review else None,
            review_topic=review.topic if review else None,
            elapsed_seconds=self.recording_seconds,
        )
        self.parallel_context_label.setText(text)
        self.parallel_context_label.setVisible(bool(text))
        self.header_stop_button.setVisible(policy.recording_busy)
        self.header_stop_button.setEnabled(
            bool(self.recorder and self.recorder.active) and not self._recording_stop_started
        )
        stop_text = (
            "Сохраняю запись…"
            if self._recording_stop_started
            else "■ Завершить запись"
        )
        self.header_stop_button.setText(stop_text)
        self.play_segment_button.setEnabled(policy.audio_playback_allowed and bool(review))

    def _load_review_lesson(self, lesson: Lesson, *, restore_form: bool | None = None) -> None:
        policy = self._parallel_policy()
        restore_form = policy.restore_recording_form if restore_form is None else restore_form

        self.review_lesson = lesson
        self._loading_segments = True
        self._summary_dirty = False
        self.transcript.clear()
        self.segment_table.setRowCount(0)

        if restore_form:
            self.audio_path.clear()
            index = self.student.findData(lesson.student.id)
            if index >= 0:
                self.student.setCurrentIndex(index)
            index = self.subject.findText(lesson.subject)
            if index >= 0:
                self.subject.setCurrentIndex(index)
            self.topic.setText(lesson.topic)
            self.lesson_date.setDate(
                QDate(lesson.lesson_date.year, lesson.lesson_date.month, lesson.lesson_date.day)
            )
            if lesson.source_audio_local and Path(lesson.source_audio_local).exists():
                self.audio_path.setText(lesson.source_audio_local)

        if lesson.artifacts.verified_transcript and Path(lesson.artifacts.verified_transcript).exists():
            self.transcript.setPlainText(
                Path(lesson.artifacts.verified_transcript).read_text(encoding="utf-8")
            )
        if lesson.artifacts.segments_json and Path(lesson.artifacts.segments_json).exists():
            self._load_segments(Path(lesson.artifacts.segments_json))
            self._restore_transcript_draft()
        self._loading_segments = False

        self.approve.setEnabled(lesson.status == JobStatus.REVIEW_REQUIRED)
        self.publish_button.setEnabled(lesson.status == JobStatus.READY)
        if lesson.status in {
            JobStatus.PUBLISHED,
            JobStatus.GENERATED_TEX,
            JobStatus.COMPILING_PDF,
            JobStatus.COMPILE_FAILED,
            JobStatus.PDF_REVIEW_REQUIRED,
        }:
            self.latex_monitor_status.setText(
                f"Восстановлено занятие: {lesson.status.value}"
            )
        self.open_pr_button.setEnabled(bool(lesson.publication and lesson.publication.pr_url))

        if lesson.status == JobStatus.REVIEW_REQUIRED:
            self._go_to(1)
        elif lesson.status == JobStatus.READY:
            self._go_to(2)
        elif lesson.status in {
            JobStatus.PUBLISHED,
            JobStatus.GENERATED_TEX,
            JobStatus.COMPILING_PDF,
            JobStatus.COMPILE_FAILED,
            JobStatus.PDF_REVIEW_REQUIRED,
        }:
            self._go_to(3)
        self._set_status(f"Занятие открыто · {lesson.student.full_name}")
        self._sync_parallel_review_ui()

    def _load_lesson(self, lesson: Lesson) -> None:
        # Startup recovery may restore the form; parallel queue review must not touch it.
        self._load_review_lesson(lesson)

    def _open_processing_item(self, item: QListWidgetItem) -> None:
        job = self.transcription_queue.get(str(item.data(256)))
        if job is None:
            return

        action = processing_action(job.status.value)
        if action == ProcessingAction.RETRY:
            answer = QMessageBox.question(
                self,
                "Ошибка транскрибации",
                (job.error or "Неизвестная ошибка")
                + "\n\nПовторить транскрибацию?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                if not job.audio.is_file():
                    QMessageBox.critical(
                        self, "Ошибка", f"Аудиофайл не найден: {job.audio}"
                    )
                    return
                job.lesson.transition(JobStatus.RECORDED, force=True)
                self.pipeline.store.save(job.lesson)
                job.lesson.write_json(self.pipeline.lesson_dir(job.lesson) / "lesson.json")
                self.transcription_queue.retry(job.id)
                self._update_transcription_queue_ui()
                self._pump_transcription_queue()
            return
        if action == ProcessingAction.WAIT:
            self._set_status("Транскрипт ещё обрабатывается", "working")
            return

        # A ready transcript is text-only and is safe to open during another recording.
        self._load_review_lesson(job.lesson, restore_form=False)

    def play_selected_segment(self, _index=None) -> None:
        if not self._parallel_policy().audio_playback_allowed:
            QMessageBox.warning(
                self,
                "Воспроизведение отключено",
                "Во время записи нельзя воспроизводить старое аудио. "
                "Оно попадёт в WASAPI Loopback. Текст транскрипта можно "
                "читать, исправлять и подтверждать.",
            )
            return
        super().play_selected_segment(_index)

    def _play_preflight_track(self, source: str) -> None:
        if not self._parallel_policy().audio_playback_allowed:
            QMessageBox.warning(
                self,
                "Воспроизведение отключено",
                "Сначала завершите текущую запись.",
            )
            return
        super()._play_preflight_track(source)

    def start_recording(self) -> None:
        # Prevent already playing media from leaking into the new loopback track.
        self.play_stop_timer.stop()
        self.player.stop()
        super().start_recording()
        self._sync_parallel_review_ui()

    def _stop_recording_async(self, reason: str | None = None) -> None:
        super()._stop_recording_async(reason)
        self._sync_parallel_review_ui()

    def _recording_ready_impl(
        self,
        result,
        recorded_lesson: Lesson,
        source_recorder,
        reason: str | None = None,
    ) -> None:
        review_before = self.review_lesson
        super()._recording_ready_impl(result, recorded_lesson, source_recorder, reason)
        # Finalizing lesson B must not replace or clear lesson A opened for review.
        if review_before is not None:
            self.review_lesson = review_before
        self._sync_parallel_review_ui()

    def _recording_stop_failed(self, details: str) -> None:
        super()._recording_stop_failed(details)
        self._sync_parallel_review_ui()

    def _tick(self) -> None:
        super()._tick()
        self._sync_parallel_review_ui()


def main() -> None:
    # Reuse the established startup/setup workflow while injecting the safe window implementation.
    base_app.MainWindow = MainWindow
    base_app.main()


if __name__ == "__main__":
    main()
