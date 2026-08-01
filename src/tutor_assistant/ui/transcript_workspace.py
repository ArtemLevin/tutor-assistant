from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .normalization_provider import provider_label, provider_models
from .theme import refresh_style, set_button_kind


class NormalizationSettingsDialog(QDialog):
    """Compact editor for the settings that are relevant to one filtering run."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        retry_requests: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки LLM-фильтрации")
        self.setModal(True)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        title = QLabel("Настройки LLM-фильтрации")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Эти параметры применяются к следующему запуску. "
            "Исходный транскрипт не изменяется автоматически."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(11)

        self.provider_combo = QComboBox()
        for item in ("ollama", "yandex_ai_studio"):
            self.provider_combo.addItem(provider_label(item), item)
        self.provider_combo.setCurrentIndex(self.provider_combo.findData(provider))
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        form.addRow("Провайдер", self.provider_combo)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        form.addRow("Модель", self.model_combo)

        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 3)
        self.retry_spin.setValue(retry_requests)
        self.retry_spin.setToolTip(
            "Количество дополнительных запросов после отклонённого ответа модели"
        )
        form.addRow("Повторных запросов", self.retry_spin)
        layout.addLayout(form)

        self.provider_hint = QLabel()
        self.provider_hint.setObjectName("muted")
        self.provider_hint.setWordWrap(True)
        layout.addWidget(self.provider_hint)

        self.credentials_group = QGroupBox("Credentials Yandex AI Studio")
        credential_layout = QVBoxLayout(self.credentials_group)
        credential_layout.setSpacing(9)
        credential_hint = QLabel(
            "API-ключ хранится в Windows Credential Manager и не записывается в YAML."
        )
        credential_hint.setObjectName("muted")
        credential_hint.setWordWrap(True)
        credential_layout.addWidget(credential_hint)
        credential_actions = QHBoxLayout()
        self.save_key_button = set_button_kind(
            QPushButton("Сохранить ключ безопасно"),
            "ghost",
        )
        self.delete_key_button = set_button_kind(
            QPushButton("Удалить сохранённый ключ"),
            "ghost",
        )
        credential_actions.addWidget(self.save_key_button)
        credential_actions.addWidget(self.delete_key_button)
        credential_actions.addStretch()
        credential_layout.addLayout(credential_actions)
        layout.addWidget(self.credentials_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._provider_changed()
        if model and self.model_combo.findText(model) < 0:
            self.model_combo.insertItem(0, model)
        self.model_combo.setCurrentText(model)

    def _provider_changed(self, _index: int | None = None) -> None:
        provider = self.selected_provider
        models = list(provider_models(provider))
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        self.model_combo.blockSignals(False)
        cloud = provider == "yandex_ai_studio"
        self.credentials_group.setVisible(cloud)
        self.provider_hint.setText(
            "Текст выбранных реплик будет передан в Yandex AI Studio после отдельного согласия."
            if cloud
            else "Обработка выполняется локально через Ollama. Текст занятия не покидает компьютер."
        )

    @property
    def selected_provider(self) -> str:
        return str(self.provider_combo.currentData() or "ollama")

    @property
    def selected_model(self) -> str:
        return self.model_combo.currentText().strip()

    @property
    def retry_requests(self) -> int:
        return int(self.retry_spin.value())


class TranscriptWorkspace(QWidget):
    """State-driven transcript editor with one contextual primary action."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 4, 2, 4)
        root.setSpacing(12)

        header = QFrame()
        header.setObjectName("transcriptHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 14, 18, 14)
        header_layout.setSpacing(14)
        heading = QVBoxLayout()
        heading.setSpacing(3)
        title = QLabel("Транскрипт занятия")
        title.setObjectName("pageTitle")
        self.context_label = QLabel("Занятие не выбрано")
        self.context_label.setObjectName("muted")
        self.context_label.setWordWrap(True)
        heading.addWidget(title)
        heading.addWidget(self.context_label)
        header_layout.addLayout(heading, 1)
        self.status_chip = QLabel("Нет занятия")
        self.status_chip.setObjectName("transcriptStatusChip")
        self.status_chip.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.status_chip, 0, Qt.AlignTop)
        root.addWidget(header)

        self.content_tabs = QTabWidget()
        self.content_tabs.setObjectName("transcriptWorkspaceTabs")
        self.content_tabs.setDocumentMode(True)
        self.content_tabs.tabBar().setExpanding(False)

        segments_page = QWidget()
        segments_layout = QVBoxLayout(segments_page)
        segments_layout.setContentsMargins(0, 8, 0, 0)
        segments_layout.setSpacing(9)
        self.segment_table = QTableWidget(0, 5)
        self.segment_table.setHorizontalHeaderLabels(
            ["Начало", "Конец", "Говорящий", "Текст", "Уверенность"]
        )
        self.segment_table.horizontalHeader().setStretchLastSection(False)
        self.segment_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.segment_table.verticalHeader().setVisible(False)
        self.segment_table.setShowGrid(False)
        self.segment_table.setAlternatingRowColors(True)
        segments_layout.addWidget(self.segment_table, 1)
        playback = QFrame()
        playback.setObjectName("transcriptToolBar")
        playback_layout = QHBoxLayout(playback)
        playback_layout.setContentsMargins(10, 7, 10, 7)
        self.play_segment_button = set_button_kind(
            QPushButton("▶  Воспроизвести сегмент"),
            "ghost",
        )
        self.playback_speed = QComboBox()
        self.playback_speed.setFixedWidth(92)
        for label, value in [("0,75×", 0.75), ("1×", 1.0), ("1,25×", 1.25)]:
            self.playback_speed.addItem(label, value)
        speed_label = QLabel("Скорость")
        speed_label.setObjectName("muted")
        playback_layout.addWidget(self.play_segment_button)
        playback_layout.addStretch()
        playback_layout.addWidget(speed_label)
        playback_layout.addWidget(self.playback_speed)
        segments_layout.addWidget(playback)
        self.segments_tab_index = self.content_tabs.addTab(segments_page, "Сегменты")

        summary_page = QWidget()
        summary_layout = QVBoxLayout(summary_page)
        summary_layout.setContentsMargins(0, 8, 0, 0)
        summary_layout.setSpacing(9)
        self.transcript_editor = QPlainTextEdit()
        self.transcript_editor.setPlaceholderText("Здесь появится распознанный текст занятия")
        summary_layout.addWidget(self.transcript_editor, 1)
        summary_actions = QFrame()
        summary_actions.setObjectName("transcriptToolBar")
        summary_actions_layout = QHBoxLayout(summary_actions)
        summary_actions_layout.setContentsMargins(10, 7, 10, 7)
        summary_hint = QLabel("Финальный шаг: подтвердите текст и перейдите к публикации")
        summary_hint.setObjectName("muted")
        summary_hint.setWordWrap(True)
        self.approve_button = set_button_kind(
            QPushButton("Подтвердить и перейти к публикации"),
            "primary",
        )
        summary_actions_layout.addWidget(summary_hint, 1)
        summary_actions_layout.addWidget(self.approve_button)
        summary_layout.addWidget(summary_actions)
        self.summary_tab_index = self.content_tabs.addTab(summary_page, "Сводный текст")

        result_page = QWidget()
        result_layout = QVBoxLayout(result_page)
        result_layout.setContentsMargins(0, 8, 0, 0)
        result_layout.setSpacing(9)
        result_header = QFrame()
        result_header.setObjectName("normalizationResultHeader")
        result_header_layout = QVBoxLayout(result_header)
        result_header_layout.setContentsMargins(14, 11, 14, 11)
        result_header_layout.setSpacing(3)
        self.result_title = QLabel("Результат фильтрации")
        self.result_title.setObjectName("normalizationStateTitle")
        self.result_summary = QLabel("Результат ещё не сформирован")
        self.result_summary.setObjectName("muted")
        self.result_summary.setWordWrap(True)
        result_header_layout.addWidget(self.result_title)
        result_header_layout.addWidget(self.result_summary)
        result_layout.addWidget(result_header)
        self.result_editor = QPlainTextEdit()
        self.result_editor.setReadOnly(True)
        self.result_editor.setPlaceholderText("Здесь появится отфильтрованный текст")
        result_layout.addWidget(self.result_editor, 1)
        self.result_warnings = QLabel()
        self.result_warnings.setObjectName("muted")
        self.result_warnings.setWordWrap(True)
        result_layout.addWidget(self.result_warnings)
        self.result_tab_index = self.content_tabs.addTab(result_page, "Результат")
        self.content_tabs.setTabVisible(self.result_tab_index, False)
        root.addWidget(self.content_tabs, 1)

        process = QFrame()
        process.setObjectName("normalizationProcessCard")
        process_layout = QVBoxLayout(process)
        process_layout.setContentsMargins(16, 13, 16, 13)
        process_layout.setSpacing(8)

        config_row = QHBoxLayout()
        config_row.setSpacing(8)
        self.config_summary = QLabel("Локальная LLM · настройки не загружены")
        self.config_summary.setObjectName("normalizationConfigSummary")
        self.config_summary.setWordWrap(True)
        self.settings_button = set_button_kind(QPushButton("Настройки"), "ghost")
        self.overflow_button = QPushButton("⋯")
        self.overflow_button.setObjectName("transcriptOverflowButton")
        self.overflow_button.setToolTip("Дополнительные действия")
        self.overflow_menu = QMenu(self.overflow_button)
        self.restart_action = self.overflow_menu.addAction("Запустить фильтрацию заново")
        self.open_artifact_action = self.overflow_menu.addAction("Открыть текстовый файл")
        self.show_warnings_action = self.overflow_menu.addAction("Показать предупреждения")
        self.overflow_menu.addSeparator()
        self.reject_action = self.overflow_menu.addAction("Отклонить результат")
        self.overflow_button.setMenu(self.overflow_menu)
        config_row.addWidget(self.config_summary, 1)
        config_row.addWidget(self.settings_button)
        config_row.addWidget(self.overflow_button)
        process_layout.addLayout(config_row)

        state_row = QHBoxLayout()
        state_text = QVBoxLayout()
        state_text.setSpacing(2)
        self.process_title = QLabel("LLM-фильтрация не запускалась")
        self.process_title.setObjectName("normalizationStateTitle")
        self.process_detail = QLabel("Проверьте транскрипт и запустите фильтрацию, когда будете готовы.")
        self.process_detail.setObjectName("muted")
        self.process_detail.setWordWrap(True)
        state_text.addWidget(self.process_title)
        state_text.addWidget(self.process_detail)
        state_row.addLayout(state_text, 1)
        self.review_result_button = set_button_kind(
            QPushButton("Проверить результат перед применением"),
            "primary",
        )
        self.review_result_button.setObjectName("normalizationReviewAction")
        self.review_result_button.setAccessibleName("Обязательная проверка результата LLM")
        self.review_result_button.setVisible(False)
        state_row.addWidget(self.review_result_button, 0, Qt.AlignBottom)
        self.primary_action_button = set_button_kind(
            QPushButton("Запустить фильтрацию"),
            "primary",
        )
        self.primary_action_button.setObjectName("transcriptPrimaryAction")
        state_row.addWidget(self.primary_action_button, 0, Qt.AlignBottom)
        process_layout.addLayout(state_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        process_layout.addWidget(self.progress)
        root.addWidget(process)
        self.process_card = process

    def set_context(self, detail: str, status: str, tone: str) -> None:
        self.context_label.setText(detail)
        self.status_chip.setText(status)
        self.status_chip.setProperty("tone", tone)
        refresh_style(self.status_chip)

    def set_segment_count(self, count: int) -> None:
        self.content_tabs.setTabText(self.segments_tab_index, f"Сегменты {count}")

    def set_config_summary(self, text: str) -> None:
        self.config_summary.setText(text)

    def set_primary_action(
        self,
        text: str,
        *,
        enabled: bool,
        kind: str = "primary",
        visible: bool = True,
    ) -> None:
        self.primary_action_button.setText(text)
        self.primary_action_button.setEnabled(enabled)
        self.primary_action_button.setVisible(visible)
        self.primary_action_button.setProperty("kind", kind)
        refresh_style(self.primary_action_button)

    def set_review_action(
        self,
        *,
        visible: bool,
        enabled: bool,
        text: str = "Проверить результат перед применением",
    ) -> None:
        self.review_result_button.setText(text)
        self.review_result_button.setEnabled(enabled)
        self.review_result_button.setVisible(visible)

    def set_process_state(
        self,
        title: str,
        detail: str,
        *,
        tone: str = "neutral",
        show_progress: bool = False,
    ) -> None:
        self.process_title.setText(title)
        self.process_detail.setText(detail)
        self.process_card.setProperty("tone", tone)
        self.progress.setVisible(show_progress)
        if not show_progress:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
        refresh_style(self.process_card)

    def set_progress(self, *, total: int, completed: int, title: str, detail: str) -> None:
        total = max(1, total)
        self.progress.setRange(0, total)
        self.progress.setValue(min(completed, total))
        self.set_process_state(title, detail, tone="working", show_progress=True)

    def clear_result(self) -> None:
        self.result_editor.clear()
        self.result_summary.setText("Результат ещё не сформирован")
        self.result_warnings.clear()
        self.content_tabs.setTabVisible(self.result_tab_index, False)
        if self.content_tabs.currentIndex() == self.result_tab_index:
            self.content_tabs.setCurrentIndex(self.summary_tab_index)

    def set_result_preview(
        self,
        text: str,
        *,
        summary: str,
        warnings: list[str] | tuple[str, ...] = (),
        select: bool = False,
    ) -> None:
        self.result_editor.setPlainText(text)
        self.result_summary.setText(summary)
        warning_count = len(warnings)
        self.result_warnings.setText(
            f"Предупреждений автоматической проверки: {warning_count}. "
            "Откройте проверку результата для подробного сравнения."
            if warning_count
            else "Автоматическая проверка не выявила дополнительных предупреждений."
        )
        self.content_tabs.setTabVisible(self.result_tab_index, True)
        if select:
            self.content_tabs.setCurrentIndex(self.result_tab_index)

    def select_summary(self) -> None:
        self.content_tabs.setCurrentIndex(self.summary_tab_index)

    def select_result(self) -> None:
        if self.content_tabs.isTabVisible(self.result_tab_index):
            self.content_tabs.setCurrentIndex(self.result_tab_index)

    def set_menu_state(
        self,
        *,
        restart: bool,
        open_artifact: bool,
        show_warnings: bool,
        reject: bool,
    ) -> None:
        self.restart_action.setEnabled(restart)
        self.open_artifact_action.setEnabled(open_artifact)
        self.show_warnings_action.setEnabled(show_warnings)
        self.reject_action.setEnabled(reject)
