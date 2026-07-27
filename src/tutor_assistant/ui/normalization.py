from __future__ import annotations

from difflib import unified_diff

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..normalization.models import NormalizedTranscript, SourceSegment
from ..normalization.prompts import render_target_text


class NormalizationReviewDialog(QDialog):
    def __init__(
        self,
        transcript: NormalizedTranscript,
        source_segments: list[SourceSegment],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.transcript = transcript
        self.source_segments = source_segments
        self.source_text = render_target_text(source_segments)
        self.setWindowTitle("Проверка нормализации транскрипта")
        self.resize(1100, 760)
        layout = QVBoxLayout(self)
        provider = transcript.normalizer.get("provider", "—")
        model = transcript.normalizer.get("model", "—")
        prompt = transcript.normalizer.get("prompt_version", "—")
        ratio = transcript.statistics.retained_ratio * 100
        summary = QLabel(
            f"Provider: {provider} · модель: {model} · промпт: {prompt} · "
            f"сохранено: {ratio:.1f}% · результат требует ручного применения"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        tabs = QTabWidget()
        tabs.addTab(self._comparison_tab(), "Изменения")
        tabs.addTab(self._source_tab(), "Исходный текст")
        tabs.addTab(self._normalized_tab(), "Нормализованный текст")
        tabs.addTab(self._warnings_tab(), "Предупреждения")
        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox()
        apply_button = buttons.addButton(
            "Применить как новую ревизию",
            QDialogButtonBox.AcceptRole,
        )
        apply_button.setToolTip("Перед применением можно отредактировать нормализованный текст")
        buttons.addButton("Закрыть без применения", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        return page, layout

    def _comparison_tab(self) -> QWidget:
        page, layout = self._page()
        hint = QLabel(
            "Строки с «-» присутствовали в исходном тексте, строки с «+» появились "
            "в нормализованной версии. Добавление содержательных фраз блокируется автоматически."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        diff = "\n".join(
            unified_diff(
                self.source_text.splitlines(),
                self.transcript.educational_text.splitlines(),
                fromfile="исходный текст",
                tofile="нормализованный текст",
                lineterm="",
            )
        )
        editor.setPlainText(diff or "Изменений нет")
        layout.addWidget(editor)
        return page

    def _source_tab(self) -> QWidget:
        page, layout = self._page()
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(self.source_text)
        layout.addWidget(editor)
        return page

    def _normalized_tab(self) -> QWidget:
        page, layout = self._page()
        hint = QLabel(
            "Это редактируемая plain-text версия. Изменения попадут в новую ревизию только "
            "после нажатия «Применить как новую ревизию»."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.normalized_editor = QPlainTextEdit()
        self.normalized_editor.setPlainText(self.transcript.educational_text)
        layout.addWidget(self.normalized_editor)
        return page

    def _warnings_tab(self) -> QWidget:
        page, layout = self._page()
        quality = self.transcript.quality
        box = QGroupBox("Автоматическая проверка")
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(
            QLabel(
                f"Plain text: {'да' if quality.plain_text_valid else 'нет'}\n"
                f"Числа сохранены: {'да' if quality.numbers_preserved else 'требуется проверка'}\n"
                f"Формульные токены сохранены: "
                f"{'да' if quality.formula_tokens_preserved else 'требуется проверка'}\n"
                f"Защищённое содержание сохранено: "
                f"{'да' if quality.protected_content_preserved else 'нет'}\n"
                f"Ручное внимание: {'да' if quality.requires_manual_attention else 'нет'}"
            )
        )
        layout.addWidget(box)
        warnings = QPlainTextEdit()
        warnings.setReadOnly(True)
        warnings.setPlainText("\n".join(quality.warnings) or "Предупреждений нет")
        layout.addWidget(warnings)
        return page

    @property
    def edited_text(self) -> str:
        return self.normalized_editor.toPlainText().strip()
