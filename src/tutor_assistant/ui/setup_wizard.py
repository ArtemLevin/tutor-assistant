from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from ..config import AppConfig
from ..latex import inspect_latex_environment
from ..recording import list_input_devices, test_input_device
from .theme import set_button_kind


class IntroPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Добро пожаловать в Tutor Assistant")
        self.setSubTitle("За несколько шагов подготовим локальное рабочее пространство.")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 22, 20, 20)
        layout.setSpacing(16)
        text = QLabel(
            "Все аудиозаписи и распознавание останутся на вашем компьютере. Мастер проверит "
            "рабочие каталоги, аудиоустройства, Git, FFmpeg, Whisper, TeX Live, Poppler и GitHub CLI."
        )
        text.setObjectName("subtitle")
        text.setWordWrap(True)
        layout.addWidget(text)
        note = QLabel("Настройки сохраняются в config/app.yaml и доступны для ручного редактирования.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()


class PathsPage(QWizardPage):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.setTitle("Рабочие каталоги")
        self.setSubTitle("Выберите, где хранить локальные занятия и где расположен репозиторий учеников.")
        form = QFormLayout(self)
        form.setContentsMargins(20, 22, 20, 20)
        form.setVerticalSpacing(14)
        self.workspace = QLineEdit(str(config.workspace))
        self.students_repo = QLineEdit(str(config.repository.students_repo))
        form.addRow("Локальные данные", self._path_row(self.workspace, False))
        form.addRow("students-26-27", self._path_row(self.students_repo, True))

    def _path_row(self, field: QLineEdit, existing: bool):
        row = QHBoxLayout()
        button = set_button_kind(QPushButton("Обзор"), "ghost")
        button.clicked.connect(lambda: self._choose(field, existing))
        row.addWidget(field)
        row.addWidget(button)
        return row

    def _choose(self, field: QLineEdit, existing: bool) -> None:
        path = QFileDialog.getExistingDirectory(self, "Выберите каталог", field.text())
        if path:
            field.setText(path)

    def validatePage(self) -> bool:
        repo = Path(self.students_repo.text()).expanduser()
        if not (repo / ".git").exists():
            QMessageBox.warning(self, "Путь", "Выбранный students-26-27 не является Git-репозиторием")
            return False
        return True


class AudioPage(QWizardPage):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.setTitle("Аудиоустройства")
        self.setSubTitle("Выберите микрофон и loopback-вход, затем проверьте уровень сигнала.")
        layout = QFormLayout(self)
        layout.setContentsMargins(20, 22, 20, 20)
        layout.setVerticalSpacing(14)
        self.mic = QComboBox()
        self.loopback = QComboBox()
        try:
            devices = list_input_devices()
        except Exception:
            devices = []
        for device in devices:
            label = f"{device.index}: {device.name} [{device.host_api}]"
            self.mic.addItem(label, device.index)
            self.loopback.addItem(label, device.index)
        self._select(self.mic, config.recording.mic_device)
        self._select(self.loopback, config.recording.loopback_device)
        self.result = QLabel("Выберите устройства и запустите тест")
        self.result.setObjectName("muted")
        self.result.setWordWrap(True)
        test = set_button_kind(QPushButton("Проверить оба устройства"), "primary")
        test.clicked.connect(lambda: self._test(config))
        layout.addRow("Микрофон", self.mic)
        layout.addRow("Системный звук", self.loopback)
        layout.addRow(test)
        layout.addRow(self.result)

    @staticmethod
    def _select(combo: QComboBox, device: int | None) -> None:
        if device is None:
            return
        index = combo.findData(device)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _test(self, config: AppConfig) -> None:
        try:
            mic = test_input_device(
                int(self.mic.currentData()),
                2,
                None,
                config.recording.channels,
            )
            system = test_input_device(
                int(self.loopback.currentData()),
                2,
                None,
                config.recording.channels,
            )
            messages = [f"Микрофон RMS: {mic.rms:.4f}", f"Системный звук RMS: {system.rms:.4f}"]
            if mic.silent:
                messages.append("Микрофон: слабый или отсутствующий сигнал")
            if system.silent:
                messages.append("Системный вход: слабый или отсутствующий сигнал")
            self.result.setText("; ".join(messages))
        except Exception as exc:
            self.result.setText(f"Ошибка проверки: {exc}")

    def validatePage(self) -> bool:
        if self.mic.currentData() is None or self.loopback.currentData() is None:
            QMessageBox.warning(self, "Аудио", "Входные аудиоустройства не найдены")
            return False
        return True


class DiagnosticsPage(QWizardPage):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.setTitle("Диагностика окружения")
        self.setSubTitle("Финальная проверка компонентов для полного конвейера занятия.")
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 22, 20, 20)
        layout.addWidget(self.summary)
        layout.addStretch()

    def initializePage(self) -> None:
        rows = []
        for command in ("git", "ffmpeg", "ffprobe"):
            rows.append((command, bool(shutil.which(command))))
        rows.append(("Python: faster-whisper", self._module("faster_whisper")))
        latex = inspect_latex_environment(self.config.latex)
        rows.append(("latexmk", bool(latex.latexmk)))
        rows.append((self.config.latex.engine, bool(latex.engine)))
        rows.append(("pdftoppm", bool(latex.pdftoppm)))
        rows.append(("GitHub CLI", bool(shutil.which("gh"))))
        if shutil.which("gh"):
            authenticated = (
                subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=15).returncode == 0
            )
            rows.append(("GitHub authentication", authenticated))
        text = "<br>".join(f"{'✓' if ok else '⚠'} {name}" for name, ok in rows)
        if latex.messages:
            text += "<br><br>LaTeX: " + "; ".join(latex.messages)
        self.summary.setText(text)

    @staticmethod
    def _module(name: str) -> bool:
        try:
            __import__(name)
            return True
        except ImportError:
            return False


class SetupWizard(QWizard):
    def __init__(self, config: AppConfig, config_path: Path) -> None:
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.setWindowTitle("Настройка Tutor Assistant")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.resize(780, 540)
        self.addPage(IntroPage())
        self.paths_page = PathsPage(config)
        self.audio_page = AudioPage(config)
        self.addPage(self.paths_page)
        self.addPage(self.audio_page)
        self.addPage(DiagnosticsPage(config))
        self.setButtonText(QWizard.WizardButton.BackButton, "Назад")
        self.setButtonText(QWizard.WizardButton.NextButton, "Продолжить")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Сохранить и открыть")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Отмена")
        set_button_kind(self.button(QWizard.WizardButton.NextButton), "primary")
        set_button_kind(self.button(QWizard.WizardButton.FinishButton), "primary")
        set_button_kind(self.button(QWizard.WizardButton.CancelButton), "ghost")

    def accept(self) -> None:
        self.config.workspace = Path(self.paths_page.workspace.text()).expanduser()
        self.config.repository.students_repo = Path(self.paths_page.students_repo.text()).expanduser()
        self.config.recording.mic_device = int(self.audio_page.mic.currentData())
        self.config.recording.loopback_device = int(self.audio_page.loopback.currentData())
        self.config.setup_completed = True
        self.config.save(self.config_path)
        super().accept()
