from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

APP_STYLESHEET = """
QWidget {
    color: #182230;
    background: #F5F7FA;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 14px;
}

QMainWindow, QDialog, QWizard, QWizardPage {
    background: #F5F7FA;
}

QToolTip {
    color: #FFFFFF;
    background: #182230;
    border: 0;
    border-radius: 6px;
    padding: 6px 8px;
}

QFrame#appHeader {
    background: #FFFFFF;
    border: 1px solid #E4E9F0;
    border-radius: 16px;
}

QFrame#statusPanel, QFrame#infoPanel {
    background: #F0F5FF;
    border: 1px solid #DCE7FA;
    border-radius: 12px;
}

QFrame#successPanel {
    background: #EDF9F4;
    border: 1px solid #CDEDDD;
    border-radius: 12px;
}

QLabel#eyebrow {
    color: #65758B;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}

QLabel#appTitle {
    color: #111827;
    font-size: 24px;
    font-weight: 700;
}

QLabel#brandMark {
    min-width: 44px;
    max-width: 44px;
    min-height: 44px;
    max-height: 44px;
    color: #FFFFFF;
    background: #356BC4;
    border-radius: 12px;
    font-size: 15px;
    font-weight: 750;
}

QLabel#pageTitle {
    color: #111827;
    font-size: 21px;
    font-weight: 700;
}

QLabel#subtitle, QLabel#muted {
    color: #65758B;
}

QLabel#timerDisplay {
    color: #111827;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 27px;
    font-weight: 600;
}

QLabel#statusPill {
    color: #216E50;
    background: #E8F7F0;
    border: 1px solid #C6EBD9;
    border-radius: 13px;
    padding: 5px 11px;
    font-size: 12px;
    font-weight: 600;
}

QLabel#statusPill[tone="working"] {
    color: #215DB0;
    background: #EAF2FF;
    border-color: #CFE0FA;
}

QLabel#statusPill[tone="warning"] {
    color: #8A5A00;
    background: #FFF7E6;
    border-color: #F3DDAA;
}

QLabel#statusPill[tone="error"] {
    color: #A33636;
    background: #FFF0F0;
    border-color: #F3CCCC;
}

QLabel#recordingState {
    color: #65758B;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.7px;
}

QLabel#recordingState[active="true"] {
    color: #C83F49;
}

QGroupBox {
    background: #FFFFFF;
    border: 1px solid #E4E9F0;
    border-radius: 14px;
    margin-top: 17px;
    padding: 20px 18px 16px 18px;
    font-weight: 650;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 15px;
    padding: 0 6px;
    color: #344054;
    background: #FFFFFF;
}

QLineEdit, QComboBox, QDateEdit, QPlainTextEdit, QTableWidget, QListWidget {
    color: #182230;
    background: #FFFFFF;
    border: 1px solid #D9E0E8;
    border-radius: 9px;
    selection-background-color: #DCEBFF;
    selection-color: #182230;
}

QLineEdit, QComboBox, QDateEdit {
    min-height: 38px;
    padding: 0 11px;
}

QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QPlainTextEdit:focus,
QTableWidget:focus, QListWidget:focus {
    border: 1px solid #4D7FD6;
}

QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled {
    color: #98A2B3;
    background: #F2F4F7;
}

QComboBox::drop-down, QDateEdit::drop-down {
    width: 30px;
    border: 0;
}

QComboBox QAbstractItemView {
    color: #182230;
    background: #FFFFFF;
    border: 1px solid #D9E0E8;
    selection-background-color: #EAF2FF;
    outline: 0;
    padding: 5px;
}

QPushButton {
    min-height: 38px;
    padding: 0 15px;
    color: #344054;
    background: #F1F4F8;
    border: 1px solid #E1E6ED;
    border-radius: 9px;
    font-weight: 600;
}

QPushButton:hover {
    background: #E8EDF3;
    border-color: #D4DBE4;
}

QPushButton:pressed {
    background: #DEE5ED;
}

QPushButton:disabled {
    color: #98A2B3;
    background: #F2F4F7;
    border-color: #EAECF0;
}

QPushButton[kind="primary"] {
    color: #FFFFFF;
    background: #356BC4;
    border-color: #356BC4;
}

QPushButton[kind="primary"]:hover {
    background: #2D5FAF;
    border-color: #2D5FAF;
}

QPushButton[kind="danger"] {
    color: #B13A45;
    background: #FFF1F2;
    border-color: #F1CDD1;
}

QPushButton[kind="danger"]:hover {
    background: #FDE7E9;
}

QPushButton[kind="ghost"] {
    color: #526174;
    background: transparent;
    border-color: #D9E0E8;
}

QTabWidget::pane {
    border: 0;
    background: transparent;
    top: 10px;
}

QTabBar::tab {
    min-width: 150px;
    min-height: 42px;
    margin-right: 6px;
    padding: 0 16px;
    color: #65758B;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    font-weight: 600;
}

QTabBar::tab:hover {
    color: #344054;
    background: #EEF2F6;
}

QTabBar::tab:selected {
    color: #275AA6;
    background: #FFFFFF;
    border-color: #DCE4ED;
}

QSplitter::handle:vertical {
    height: 6px;
    margin: 2px 80px;
    background: #D7DEE8;
    border-radius: 3px;
}

QSplitter::handle:vertical:hover {
    background: #9FB3CF;
}

QHeaderView::section {
    min-height: 38px;
    padding: 0 10px;
    color: #526174;
    background: #F7F9FB;
    border: 0;
    border-bottom: 1px solid #E4E9F0;
    font-size: 12px;
    font-weight: 650;
}

QTableWidget {
    gridline-color: #EEF1F5;
    alternate-background-color: #FAFBFC;
}

QTableWidget::item {
    padding: 7px;
    border-bottom: 1px solid #F0F2F5;
}

QTableWidget::item:selected, QListWidget::item:selected {
    color: #182230;
    background: #EAF2FF;
}

QPlainTextEdit {
    padding: 10px;
    font-family: "Cascadia Mono", "Consolas", monospace;
}

QProgressBar {
    min-height: 8px;
    max-height: 8px;
    color: transparent;
    background: #E9EDF2;
    border: 0;
    border-radius: 4px;
}

QProgressBar::chunk {
    background: #4F80CF;
    border-radius: 4px;
}

QCheckBox {
    spacing: 8px;
}

QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border: 1px solid #C7D0DB;
    border-radius: 5px;
    background: #FFFFFF;
}

QCheckBox::indicator:checked {
    background: #356BC4;
    border-color: #356BC4;
}

QScrollBar:vertical {
    width: 10px;
    margin: 2px;
    background: transparent;
}

QScrollBar::handle:vertical {
    min-height: 28px;
    background: #C9D1DB;
    border-radius: 5px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}

QStatusBar {
    color: #65758B;
    background: #F5F7FA;
    border-top: 1px solid #E4E9F0;
}

QMessageBox, QFileDialog {
    background: #F5F7FA;
}

QWizard QPushButton {
    min-width: 100px;
}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#F5F7FA"))
    palette.setColor(QPalette.WindowText, QColor("#182230"))
    palette.setColor(QPalette.Base, QColor("#FFFFFF"))
    palette.setColor(QPalette.AlternateBase, QColor("#FAFBFC"))
    palette.setColor(QPalette.Text, QColor("#182230"))
    palette.setColor(QPalette.Button, QColor("#F1F4F8"))
    palette.setColor(QPalette.ButtonText, QColor("#344054"))
    palette.setColor(QPalette.Highlight, QColor("#DCEBFF"))
    palette.setColor(QPalette.HighlightedText, QColor("#182230"))
    app.setPalette(palette)
    app.setStyleSheet(APP_STYLESHEET)


def set_button_kind(button: QPushButton, kind: str) -> QPushButton:
    button.setProperty("kind", kind)
    return button


def set_status(label: QLabel, text: str, tone: str = "success") -> None:
    label.setText(text)
    label.setProperty("tone", tone)
    label.style().unpolish(label)
    label.style().polish(label)
    label.update()


def refresh_style(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
