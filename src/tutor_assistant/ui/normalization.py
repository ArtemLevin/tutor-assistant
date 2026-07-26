from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..normalization.models import NormalizedTranscript, SourceSegment


def _time_range(segment: SourceSegment) -> str:
    if segment.start is None and segment.end is None:
        return "—"
    start = "—" if segment.start is None else f"{segment.start:.2f}"
    end = "—" if segment.end is None else f"{segment.end:.2f}"
    return f"{start}–{end}"


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
        self.setWindowTitle("Проверка локальной нормализации")
        self.resize(1100, 760)
        layout = QVBoxLayout(self)
        model = transcript.normalizer.get("model", "—")
        prompt = transcript.normalizer.get("prompt_version", "—")
        ratio = transcript.statistics.retained_ratio * 100
        summary = QLabel(
            f"Модель: {model} · промпт: {prompt} · сохранено: {ratio:.1f}% · "
            "результат не применяется автоматически"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        tabs = QTabWidget()
        tabs.addTab(self._comparison_tab(), "Сравнение")
        tabs.addTab(self._source_tab(), "Исходный текст")
        tabs.addTab(self._normalized_tab(), "Нормализованный текст")
        tabs.addTab(self._removed_tab(), "Удалённые фрагменты")
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
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(
            ["Время", "Роль", "Действие", "Категория", "Исходный текст", "Новый текст", "ID"]
        )
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        normalized = {item.source_segment_ids[0]: item for item in self.transcript.segments}
        removed = {item.source_segment_ids[0]: item for item in self.transcript.removed_fragments}
        table.setRowCount(len(self.source_segments))
        for row, source in enumerate(self.source_segments):
            kept = normalized.get(source.source_segment_id)
            dropped = removed.get(source.source_segment_id)
            if dropped:
                action = "drop"
                category = dropped.category
                new_text = ""
            elif kept:
                new_text = kept.text
                action = "keep" if new_text == source.text.strip() else "trim"
                category = kept.content_type
            else:
                action = "?"
                category = "unknown"
                new_text = ""
            values = (
                _time_range(source),
                source.speaker or "—",
                action,
                category,
                source.text,
                new_text,
                str(source.source_segment_id),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column not in {4, 5}:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, column, item)
        layout.addWidget(table)
        return page

    def _source_tab(self) -> QWidget:
        page, layout = self._page()
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(
            "\n".join(
                f"[{segment.speaker}] {segment.text}" if segment.speaker else segment.text
                for segment in self.source_segments
            )
        )
        layout.addWidget(editor)
        return page

    def _normalized_tab(self) -> QWidget:
        page, layout = self._page()
        hint = QLabel(
            "Это редактируемая версия. Изменения попадут в новую ревизию только после нажатия "
            "«Применить как новую ревизию»."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.normalized_editor = QPlainTextEdit()
        self.normalized_editor.setPlainText(self.transcript.educational_text)
        layout.addWidget(self.normalized_editor)
        return page

    def _removed_tab(self) -> QWidget:
        page, layout = self._page()
        source = {item.source_segment_id: item for item in self.source_segments}
        table = QTableWidget(len(self.transcript.removed_fragments), 5)
        table.setHorizontalHeaderLabels(["Время", "Роль", "Категория", "Причина", "Текст"])
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        for row, removed in enumerate(self.transcript.removed_fragments):
            original = source.get(removed.source_segment_ids[0])
            values = (
                _time_range(original) if original else "—",
                original.speaker if original and original.speaker else "—",
                removed.category,
                removed.reason_code,
                removed.text or (original.text if original else "—"),
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(table)
        return page

    def _warnings_tab(self) -> QWidget:
        page, layout = self._page()
        quality = self.transcript.quality
        box = QGroupBox("Автоматическая проверка")
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(
            QLabel(
                f"Все сегменты классифицированы: "
                f"{'да' if quality.all_source_segments_classified else 'нет'}\n"
                f"Числа сохранены: {'да' if quality.numbers_preserved else 'требуется проверка'}\n"
                f"Формульные токены сохранены: "
                f"{'да' if quality.formula_tokens_preserved else 'требуется проверка'}\n"
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
