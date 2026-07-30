from __future__ import annotations

import logging

from PySide6.QtWidgets import QLabel, QMessageBox

from ..domain import JobStatus
from ..publisher import publication_payload_files
from . import app as base_app
from .concurrent_app import MainWindow as ConcurrentMainWindow


class MainWindow(ConcurrentMainWindow):
    """Production window with explicit transcript-only publication UX."""

    def __init__(self, config_path):
        super().__init__(config_path)
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
