from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtWidgets import QComboBox

SUBJECT_LABELS: dict[str, str] = {
    "mathematics": "Математика",
    "physics": "Физика",
    "chemistry": "Химия",
}

CONTACT_CHANNEL_LABELS: dict[str, str] = {
    "phone": "Телефон",
    "email": "Электронная почта",
    "social": "Социальная сеть",
}

_SUBJECT_ALIASES = {
    alias.casefold(): value
    for value, label in SUBJECT_LABELS.items()
    for alias in (value, label)
}


def subject_label(value: str | None) -> str:
    normalized = (value or "").strip()
    return SUBJECT_LABELS.get(normalized.casefold(), normalized)


def subject_value(value: str | None) -> str:
    normalized = (value or "").strip()
    return _SUBJECT_ALIASES.get(normalized.casefold(), normalized)


def subject_items(values: Iterable[str] | None = None) -> tuple[tuple[str, str], ...]:
    source = tuple(values) if values is not None else tuple(SUBJECT_LABELS)
    return tuple((subject_label(value), subject_value(value)) for value in source)


def set_subject_combo(
    combo: QComboBox,
    values: Iterable[str] | None = None,
    *,
    selected: str | None = None,
) -> None:
    current = subject_value(selected or combo.currentData() or combo.currentText())
    editable = combo.isEditable()
    combo.blockSignals(True)
    combo.clear()
    for label, value in subject_items(values):
        combo.addItem(label, value)
    combo.setEditable(editable)
    combo.blockSignals(False)
    select_subject(combo, current)


def select_subject(combo: QComboBox, value: str | None) -> bool:
    canonical = subject_value(value)
    index = combo.findData(canonical)
    if index >= 0:
        combo.setCurrentIndex(index)
        return True
    if combo.isEditable() and canonical:
        combo.setCurrentText(subject_label(canonical))
        return True
    return False


def subject_list_text(values: Iterable[str]) -> str:
    return ", ".join(subject_label(value) for value in values)


def parse_subject_list(value: str) -> list[str]:
    result: list[str] = []
    for item in value.split(","):
        canonical = subject_value(item)
        if canonical and canonical not in result:
            result.append(canonical)
    return result


def contact_channel_label(value: str | None) -> str:
    normalized = (value or "").strip()
    return CONTACT_CHANNEL_LABELS.get(normalized.casefold(), normalized)
