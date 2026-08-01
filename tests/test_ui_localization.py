from __future__ import annotations

from PySide6.QtWidgets import QApplication, QComboBox

from tutor_assistant.ui.localization import (
    contact_channel_label,
    parse_subject_list,
    select_subject,
    set_subject_combo,
    subject_label,
    subject_list_text,
    subject_value,
)

_APPLICATION: QApplication | None = None


def _application() -> QApplication:
    global _APPLICATION
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        _APPLICATION = existing
    elif _APPLICATION is None:
        _APPLICATION = QApplication([])
    return _APPLICATION


def test_subject_combo_shows_russian_and_keeps_canonical_values() -> None:
    _application()
    combo = QComboBox()
    set_subject_combo(combo, selected="physics")

    assert combo.currentText() == "Физика"
    assert combo.currentData() == "physics"
    assert combo.itemText(combo.findData("mathematics")) == "Математика"

    assert select_subject(combo, "Химия") is True
    assert combo.currentData() == "chemistry"


def test_subject_and_contact_enums_round_trip() -> None:
    assert subject_label("mathematics") == "Математика"
    assert subject_value("Математика") == "mathematics"
    assert subject_list_text(["mathematics", "physics"]) == "Математика, Физика"
    assert parse_subject_list("Математика, chemistry") == ["mathematics", "chemistry"]
    assert contact_channel_label("social") == "Социальная сеть"
