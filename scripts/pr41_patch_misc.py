from pathlib import Path


def once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError(f"{label} marker mismatch")
    return source.replace(old, new, 1)


workspace_path = Path("src/tutor_assistant/ui/transcript_workspace.py")
workspace = workspace_path.read_text(encoding="utf-8")
workspace = once(
    workspace,
    '''        provider = self.selected_provider
        current = self.model_combo.currentText().strip() if self.model_combo.count() else ""
        models = list(provider_models(provider))
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if current and current not in models:
            self.model_combo.insertItem(0, current)
        if current:
            self.model_combo.setCurrentText(current)
        self.model_combo.blockSignals(False)
''',
    '''        provider = self.selected_provider
        models = list(provider_models(provider))
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        self.model_combo.blockSignals(False)
''',
    "provider model catalog",
)
workspace_path.write_text(workspace, encoding="utf-8")


theme_path = Path("src/tutor_assistant/ui/theme.py")
theme = theme_path.read_text(encoding="utf-8")
transcript_styles = '''QFrame#transcriptHeader {
    background: #FFFFFF;
    border: 1px solid #E4E9F0;
    border-radius: 14px;
}

QFrame#transcriptToolBar {
    background: #F8FAFD;
    border: 1px solid #E4E9F0;
    border-radius: 10px;
}

QFrame#normalizationProcessCard {
    background: #FFFFFF;
    border: 1px solid #DCE4ED;
    border-radius: 14px;
}

QFrame#normalizationProcessCard[tone="working"] {
    background: #F0F5FF;
    border-color: #CFE0FA;
}

QFrame#normalizationProcessCard[tone="warning"] {
    background: #FFF9ED;
    border-color: #F3DDAA;
}

QFrame#normalizationProcessCard[tone="error"] {
    background: #FFF3F3;
    border-color: #F3CCCC;
}

QFrame#normalizationProcessCard[tone="success"] {
    background: #F0FAF5;
    border-color: #CDEDDD;
}

QFrame#normalizationResultHeader {
    background: #F0F5FF;
    border: 1px solid #DCE7FA;
    border-radius: 11px;
}

QLabel#transcriptStatusChip {
    color: #526174;
    background: #EEF2F6;
    border: 1px solid #DCE4ED;
    border-radius: 12px;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 650;
}

QLabel#transcriptStatusChip[tone="success"] {
    color: #216E50;
    background: #E8F7F0;
    border-color: #C6EBD9;
}

QLabel#transcriptStatusChip[tone="warning"] {
    color: #8A5A00;
    background: #FFF7E6;
    border-color: #F3DDAA;
}

QLabel#transcriptStatusChip[tone="error"] {
    color: #A33636;
    background: #FFF0F0;
    border-color: #F3CCCC;
}

QLabel#normalizationStateTitle {
    color: #243348;
    font-size: 15px;
    font-weight: 700;
}

QLabel#normalizationConfigSummary {
    color: #526174;
    font-size: 12px;
    font-weight: 600;
}

QPushButton#transcriptPrimaryAction {
    min-width: 190px;
}

QPushButton#transcriptOverflowButton {
    min-width: 40px;
    max-width: 40px;
    padding: 0;
    font-size: 18px;
    font-weight: 700;
}

QTabWidget#transcriptWorkspaceTabs QTabBar::tab {
    min-width: 118px;
    min-height: 38px;
    padding: 0 14px;
}

QMenu {
    color: #182230;
    background: #FFFFFF;
    border: 1px solid #D9E0E8;
    border-radius: 9px;
    padding: 6px;
}

QMenu::item {
    padding: 8px 18px;
    border-radius: 6px;
}

QMenu::item:selected {
    background: #EAF2FF;
}

'''
theme = once(theme, "QStatusBar {\n", transcript_styles + "QStatusBar {\n", "theme insertion")
theme_path.write_text(theme, encoding="utf-8")


test_path = Path("tests/test_transcript_workspace_gui.py")
test_source = test_path.read_text(encoding="utf-8")
test_source = once(
    test_source,
    "    assert workspace.progress.isVisible() is True\n",
    "    assert workspace.progress.isHidden() is False\n",
    "progress visibility assertion",
)
test_path.write_text(test_source, encoding="utf-8")


workflow_path = Path(".github/workflows/windows-content.yml")
workflow = workflow_path.read_text(encoding="utf-8")
workflow = once(
    workflow,
    "          tests/test_normalization_progress_gui.py\n",
    "          tests/test_normalization_progress_gui.py\n"
    "          tests/test_transcript_workspace_gui.py\n",
    "focused GUI test",
)
Path("scripts/pr41_windows_content_product.txt").write_text(workflow, encoding="utf-8")


readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
readme = once(readme, "Текущая версия: **0.16.0**.", "Текущая версия: **0.17.0**.", "README version")
readme = once(
    readme,
    '''В GUI провайдер выбирается непосредственно над редактором транскрипта:
**«Локальная LLM (Ollama)»** или **«Yandex AI Studio»**. При первом выборе Yandex
приложение запрашивает `folder_id` и явное согласие на передачу текста в облако.
''',
    '''В GUI вкладка «Транскрипт» организована как последовательное рабочее пространство:
сегменты, сводный текст и результат фильтрации открываются во внутренних вкладках,
а основное действие меняется вместе с состоянием запуска. Провайдер, модель, число
повторных запросов и credentials Yandex вынесены в отдельное окно настроек. При
первом выборе Yandex приложение запрашивает `folder_id` и явное согласие на передачу
текста в облако.
''',
    "README transcript workspace",
)
readme_path.write_text(readme, encoding="utf-8")


pyproject_path = Path("pyproject.toml")
pyproject = pyproject_path.read_text(encoding="utf-8")
pyproject = once(pyproject, 'version = "0.16.0"', 'version = "0.17.0"', "project version")
pyproject_path.write_text(pyproject, encoding="utf-8")


init_path = Path("src/tutor_assistant/__init__.py")
init_source = init_path.read_text(encoding="utf-8")
init_source = once(init_source, '__version__ = "0.16.0"', '__version__ = "0.17.0"', "package version")
init_path.write_text(init_source, encoding="utf-8")

print("misc patch applied")