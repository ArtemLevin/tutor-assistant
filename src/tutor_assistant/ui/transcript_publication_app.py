from __future__ import annotations

import logging

from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QMessageBox

from . import app as base_app
from .concurrent_app import MainWindow as ConcurrentMainWindow
from ..domain import JobStatus
from ..publisher import publication_payload_files
from ..recording import DualRecorder


_AUDIO_FORMAT_OPTIONS = (
    ("M4A · AAC 96 кбит/с · рекомендуется", "m4a"),
    ("MP3 · 128 кбит/с", "mp3"),
    ("WAV · PCM 16 бит", "wav"),
)


class MainWindow(ConcurrentMainWindow):
    """Production window with transcript-only publication and audio delivery controls."""

    def __init__(self, config_path):
        super().__init__(config_path)
        self._install_audio_format_selector()
        self.open_pr_button.setVisible(False)
        self.publish_button.setText("Опубликовать transcript.txt в main")
        self.publish_button.setToolTip(
            "Передать в приватный репозиторий только подтверждённый transcript.txt"
        )
        publication_page = self.tabs.widget(2)
        for label in publication_page.findChildren(QLabel):
            if label.text() == "Опубликуйте материалы":
                label.setText("Опубликуйте транскрипт")
            elif label.text() == (
                "Приложение создаст изолированную ветку занятия и draft pull request для проверки."
            ):
                label.setText(
                    "После подтверждения в main будет записан один файл transcript.txt. "
                    "Аудио и служебные материалы останутся локально."
                )

    def _install_audio_format_selector(self) -> None:
        self.audio_output_format = QComboBox()
        self.audio_output_format.setObjectName("audioOutputFormat")
        self.audio_output_format.setToolTip(
            "Чанки и внутренний мастер сохраняются в WAV. "
            "Итоговый файл кодируется после завершения записи."
        )
        for label, value in _AUDIO_FORMAT_OPTIONS:
            self.audio_output_format.addItem(label, value)
        selected_index = self.audio_output_format.findData(
            self.config.recording.output_format
        )
        if selected_index >= 0:
            self.audio_output_format.setCurrentIndex(selected_index)
        form = self.student.parentWidget().layout()
        if not isinstance(form, QFormLayout):
            raise RuntimeError("Форма параметров занятия недоступна")
        form.addRow("Итоговый формат аудио", self.audio_output_format)
        self.audio_output_format.currentIndexChanged.connect(
            self._audio_output_format_changed
        )
        DualRecorder.set_default_output_format(self.config.recording.output_format)

    def _audio_output_format_changed(self, _index: int) -> None:
        selected = str(self.audio_output_format.currentData())
        self.config.recording.output_format = selected
        DualRecorder.set_default_output_format(selected)
        self.config.save(self.config_path)
        self._set_status(f"Формат следующих записей: {selected.upper()}")

    def start_recording(self) -> None:
        self.audio_output_format.setEnabled(False)
        try:
            super().start_recording()
        finally:
            if not (self.recorder and self.recorder.active):
                self.audio_output_format.setEnabled(True)

    def _recording_ready(self, *args, **kwargs) -> None:
        try:
            super()._recording_ready(*args, **kwargs)
        finally:
            self.audio_output_format.setEnabled(True)

    def _recording_stop_failed(self, *args, **kwargs) -> None:
        try:
            super()._recording_stop_failed(*args, **kwargs)
        finally:
            self.audio_output_format.setEnabled(True)

    def approve_transcript(self) -> None:
        super().approve_transcript()
        if not self.lesson or self.lesson.status != JobStatus.READY:
            return
        payload = "\n".join(f"• {path}" for path in publication_payload_files(self.lesson))
        self.publish_summary.setText(
            f"{self.lesson.student.full_name}\n"
            f"{self.lesson.lesson_date:%d.%m.%Y}\n"
            f"{self.lesson.topic}\n\n"
            "Будет опубликован ровно один файл:\n"
            f"{payload}\n\n"
            "Ветка: main\n"
            "Аудио, JSON, TEX, PDF, изображения и журналы останутся на компьютере."
        )

    def publish(self) -> None:
        if not self.lesson or self.lesson.status != JobStatus.READY:
            QMessageBox.warning(self, "Публикация", "Сначала подтвердите транскрипт")
            return
        self.publish_button.setEnabled(False)
        self._set_status("Публикую transcript.txt в main…", "working")
        logging.info("Transcript-only публикация начата: lesson=%s", self.lesson.lesson_id)
        worker = base_app.Worker(self.pipeline.publish, self.lesson)
        worker.succeeded.connect(self._publication_ready)
        worker.failed.connect(lambda details: self._operation_failed("publish", details))
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.workers.append(worker)
        worker.start()

    def _publication_ready(self, result) -> None:
        details = (
            "Опубликован один файл transcript.txt\n"
            f"Ветка: {result.branch}\n"
            f"Commit: {result.commit[:12]}\n"
            f"Путь: {result.repository_path}\n\n"
            "Остальные файлы занятия сохранены только локально."
        )
        if result.warnings:
            details += "\n\n" + "\n".join(result.warnings)
        QMessageBox.information(self, "Публикация завершена", details)
        self.latex_monitor_status.setText(
            "Удалённая публикация содержит только transcript.txt; производные материалы не отправляются"
        )
        self._set_status("transcript.txt опубликован в main")
        self.publish_summary.setText(details)
        logging.info(
            "Transcript-only публикация завершена: branch=%s commit=%s path=%s",
            result.branch,
            result.commit,
            result.repository_path,
        )


def main() -> None:
    base_app.MainWindow = MainWindow
    base_app.main()


if __name__ == "__main__":
    main()
