from __future__ import annotations

from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"Expected exactly one match in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


write(
    "src/tutor_assistant/ui/normalization_provider.py",
    r'''
    from __future__ import annotations

    import os

    from ..config import NormalizationConfig

    PROVIDER_LABELS = {
        "ollama": "Локальная LLM (Ollama)",
        "yandex_ai_studio": "Yandex AI Studio",
    }

    PROVIDER_MODELS = {
        "ollama": ("qwen3:8b", "qwen3:14b"),
        "yandex_ai_studio": ("yandexgpt-lite", "yandexgpt"),
    }


    def provider_label(provider: str) -> str:
        try:
            return PROVIDER_LABELS[provider]
        except KeyError as exc:
            raise ValueError(f"Неизвестный провайдер LLM-фильтрации: {provider}") from exc


    def provider_models(provider: str) -> tuple[str, ...]:
        try:
            return PROVIDER_MODELS[provider]
        except KeyError as exc:
            raise ValueError(f"Неизвестный провайдер LLM-фильтрации: {provider}") from exc


    def select_provider_config(
        current: NormalizationConfig,
        provider: str,
        *,
        folder_id: str | None = None,
        allow_cloud_processing: bool | None = None,
    ) -> NormalizationConfig:
        payload = current.model_dump()
        payload["provider"] = provider
        if provider == "yandex_ai_studio":
            payload["allow_cloud_processing"] = (
                current.allow_cloud_processing
                if allow_cloud_processing is None
                else allow_cloud_processing
            )
            selected_folder = folder_id if folder_id is not None else current.yandex_folder_id
            payload["yandex_folder_id"] = (selected_folder or "").strip() or None
        return NormalizationConfig.model_validate(payload)


    def with_provider_model(
        current: NormalizationConfig,
        provider: str,
        model: str,
    ) -> NormalizationConfig:
        selected = model.strip()
        if not selected:
            raise ValueError("Укажите модель LLM-фильтрации")
        payload = current.model_dump()
        payload["provider"] = provider
        if provider == "yandex_ai_studio":
            payload["yandex_model"] = selected
        elif provider == "ollama":
            payload["model"] = selected
        else:
            raise ValueError(f"Неизвестный провайдер LLM-фильтрации: {provider}")
        return NormalizationConfig.model_validate(payload)


    def provider_configuration_error(config: NormalizationConfig) -> str | None:
        if config.provider != "yandex_ai_studio":
            return None
        if not config.allow_cloud_processing:
            return "Передача транскрипта в Yandex AI Studio не разрешена"
        if not (config.yandex_folder_id or "").strip():
            return "Не указан Yandex Cloud folder ID"
        if not os.getenv(config.yandex_api_key_env, "").strip():
            return (
                f"Не задана переменная окружения {config.yandex_api_key_env}. "
                "API-ключ не сохраняется в конфигурации приложения."
            )
        return None


    def provider_hint(config: NormalizationConfig) -> str:
        if config.provider == "ollama":
            return "Локальная обработка: текст занятия не отправляется в облако."
        folder = (config.yandex_folder_id or "не указан").strip()
        key_ready = bool(os.getenv(config.yandex_api_key_env, "").strip())
        key_state = "ключ найден" if key_ready else f"задайте {config.yandex_api_key_env}"
        return f"Облачная обработка · folder: {folder} · {key_state}."
    ''',
)

write(
    "tests/test_normalization_provider_gui.py",
    r'''
    from __future__ import annotations

    import pytest

    from tutor_assistant.config import NormalizationConfig
    from tutor_assistant.ui.normalization_provider import (
        provider_configuration_error,
        provider_hint,
        provider_label,
        provider_models,
        select_provider_config,
        with_provider_model,
    )


    def test_provider_catalog_exposes_local_and_yandex_choices() -> None:
        assert provider_label("ollama") == "Локальная LLM (Ollama)"
        assert provider_label("yandex_ai_studio") == "Yandex AI Studio"
        assert provider_models("ollama")[0] == "qwen3:8b"
        assert provider_models("yandex_ai_studio")[0] == "yandexgpt-lite"


    def test_switch_to_yandex_requires_explicit_cloud_consent_and_folder() -> None:
        current = NormalizationConfig()

        with pytest.raises(ValueError, match="allow_cloud_processing"):
            select_provider_config(
                current,
                "yandex_ai_studio",
                folder_id="folder-id",
                allow_cloud_processing=False,
            )

        selected = select_provider_config(
            current,
            "yandex_ai_studio",
            folder_id="folder-id",
            allow_cloud_processing=True,
        )

        assert selected.provider == "yandex_ai_studio"
        assert selected.allow_cloud_processing is True
        assert selected.yandex_folder_id == "folder-id"


    def test_provider_models_are_persisted_independently() -> None:
        current = NormalizationConfig()
        local = with_provider_model(current, "ollama", "qwen3:14b")
        cloud = select_provider_config(
            local,
            "yandex_ai_studio",
            folder_id="folder-id",
            allow_cloud_processing=True,
        )
        cloud = with_provider_model(cloud, "yandex_ai_studio", "yandexgpt")
        back_to_local = select_provider_config(cloud, "ollama")

        assert cloud.yandex_model == "yandexgpt"
        assert back_to_local.model == "qwen3:14b"
        assert back_to_local.effective_model == "qwen3:14b"


    def test_yandex_readiness_uses_environment_without_persisting_key(monkeypatch) -> None:
        config = select_provider_config(
            NormalizationConfig(),
            "yandex_ai_studio",
            folder_id="folder-id",
            allow_cloud_processing=True,
        )
        monkeypatch.delenv(config.yandex_api_key_env, raising=False)
        assert config.yandex_api_key_env in (provider_configuration_error(config) or "")
        assert "задайте" in provider_hint(config)

        monkeypatch.setenv(config.yandex_api_key_env, "secret")
        assert provider_configuration_error(config) is None
        assert "ключ найден" in provider_hint(config)
    ''',
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    "    QHeaderView,\n    QLabel,\n    QLineEdit,\n",
    "    QHeaderView,\n    QInputDialog,\n    QLabel,\n    QLineEdit,\n",
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    "from .normalization import NormalizationReviewDialog\n",
    "from .normalization import NormalizationReviewDialog\n"
    "from .normalization_provider import (\n"
    "    provider_configuration_error,\n"
    "    provider_hint,\n"
    "    provider_label,\n"
    "    provider_models,\n"
    "    select_provider_config,\n"
    "    with_provider_model,\n"
    ")\n",
)

old_controls = '''        normalization_controls = QHBoxLayout()
        provider_label = (
            "Yandex AI Studio"
            if self.config.normalization.provider == "yandex_ai_studio"
            else "Локальный Ollama"
        )
        normalization_label = QLabel(f"LLM-фильтр · {provider_label}")
        normalization_label.setObjectName("muted")
        normalization_controls.addWidget(normalization_label)
        self.normalization_model = QComboBox()
        self.normalization_model.setEditable(True)
        models = (
            ["yandexgpt-lite", "yandexgpt"]
            if self.config.normalization.provider == "yandex_ai_studio"
            else ["qwen3:8b", "qwen3:14b"]
        )
        self.normalization_model.addItems(models)
        self.normalization_model.setCurrentText(self.config.normalization.effective_model)
        self.normalization_model.setMinimumWidth(145)
        normalization_controls.addWidget(self.normalization_model)
'''

new_controls = '''        normalization_controls = QHBoxLayout()
        normalization_label = QLabel("LLM-фильтр")
        normalization_label.setObjectName("muted")
        normalization_controls.addWidget(normalization_label)
        self.normalization_provider = QComboBox()
        for provider in ("ollama", "yandex_ai_studio"):
            self.normalization_provider.addItem(provider_label(provider), provider)
        self.normalization_provider.setCurrentIndex(
            self.normalization_provider.findData(self.config.normalization.provider)
        )
        self.normalization_provider.setMinimumWidth(205)
        self.normalization_provider.currentIndexChanged.connect(
            self._normalization_provider_changed
        )
        normalization_controls.addWidget(self.normalization_provider)
        self.normalization_model = QComboBox()
        self.normalization_model.setEditable(True)
        self.normalization_model.setMinimumWidth(145)
        normalization_controls.addWidget(self.normalization_model)
'''
replace_once("src/tutor_assistant/ui/app.py", old_controls, new_controls)

replace_once(
    "src/tutor_assistant/ui/app.py",
    "        normalization_controls.addStretch()\n"
    "        segments_layout.addLayout(normalization_controls)\n"
    "        summary = QGroupBox(\"Сводный текст\")\n",
    "        normalization_controls.addStretch()\n"
    "        segments_layout.addLayout(normalization_controls)\n"
    "        self.normalization_provider_hint = QLabel()\n"
    "        self.normalization_provider_hint.setObjectName(\"muted\")\n"
    "        self.normalization_provider_hint.setWordWrap(True)\n"
    "        segments_layout.addWidget(self.normalization_provider_hint)\n"
    "        self._sync_normalization_provider_ui()\n"
    "        summary = QGroupBox(\"Сводный текст\")\n",
)

provider_methods = r'''
    def _selected_normalization_provider(self) -> str:
        if not hasattr(self, "normalization_provider"):
            return self.config.normalization.provider
        return str(self.normalization_provider.currentData() or "ollama")

    def _set_normalization_provider_combo(self, provider: str) -> None:
        if not hasattr(self, "normalization_provider"):
            return
        self.normalization_provider.blockSignals(True)
        self.normalization_provider.setCurrentIndex(
            self.normalization_provider.findData(provider)
        )
        self.normalization_provider.blockSignals(False)

    def _replace_normalization_config(self, config) -> None:
        self.config.normalization = config
        self.config.save(self.config_path)
        self.normalization_service = NormalizationService(
            config,
            self.content_service,
        )

    def _sync_normalization_provider_ui(self) -> None:
        if not hasattr(self, "normalization_provider"):
            return
        provider = self._selected_normalization_provider()
        configured_model = (
            self.config.normalization.yandex_model
            if provider == "yandex_ai_studio"
            else self.config.normalization.model
        )
        models = list(provider_models(provider))
        if configured_model and configured_model not in models:
            models.insert(0, configured_model)
        self.normalization_model.blockSignals(True)
        self.normalization_model.clear()
        self.normalization_model.addItems(models)
        self.normalization_model.setCurrentText(configured_model)
        self.normalization_model.blockSignals(False)
        self.normalization_provider_hint.setText(provider_hint(self.config.normalization))
        error = provider_configuration_error(self.config.normalization)
        tooltip = error or provider_hint(self.config.normalization)
        self.normalization_provider.setToolTip(tooltip)
        self.normalization_model.setToolTip(tooltip)

    def _normalization_provider_changed(self, _index: int) -> None:
        selected = self._selected_normalization_provider()
        current = self.config.normalization.provider
        if selected == current:
            self._sync_normalization_provider_ui()
            return
        if self._normalization_cancellation is not None:
            self._set_normalization_provider_combo(current)
            QMessageBox.warning(
                self,
                "LLM-фильтрация",
                "Нельзя менять провайдера во время выполняющейся фильтрации.",
            )
            return

        folder_id = self.config.normalization.yandex_folder_id
        allow_cloud_processing = self.config.normalization.allow_cloud_processing
        if selected == "yandex_ai_studio":
            if not allow_cloud_processing:
                answer = QMessageBox.question(
                    self,
                    "Облачная обработка транскрипта",
                    "Текст занятия будет передан в Yandex AI Studio. "
                    "Аудиозапись не отправляется. Разрешить облачную обработку?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self._set_normalization_provider_combo(current)
                    return
                allow_cloud_processing = True
            if not (folder_id or "").strip():
                folder_id, accepted = QInputDialog.getText(
                    self,
                    "Yandex AI Studio",
                    "Yandex Cloud folder ID:",
                    text="",
                )
                if not accepted or not folder_id.strip():
                    self._set_normalization_provider_combo(current)
                    return

        try:
            updated = select_provider_config(
                self.config.normalization,
                selected,
                folder_id=folder_id,
                allow_cloud_processing=allow_cloud_processing,
            )
            self._replace_normalization_config(updated)
        except Exception as exc:
            self._set_normalization_provider_combo(current)
            QMessageBox.warning(self, "LLM-фильтрация", str(exc))
            return

        self._sync_normalization_provider_ui()
        self._sync_normalization_controls()
        error = provider_configuration_error(updated)
        if error:
            QMessageBox.information(
                self,
                "Настройка Yandex AI Studio",
                error
                + "\n\nЗадайте API-ключ в переменной окружения и перезапустите приложение.",
            )
        else:
            self._set_status(f"Провайдер LLM-фильтрации: {provider_label(selected)}")

    def _persist_selected_normalization_model(self) -> str:
        provider = self._selected_normalization_provider()
        model = self.normalization_model.currentText().strip()
        updated = with_provider_model(self.config.normalization, provider, model)
        if updated != self.config.normalization:
            self._replace_normalization_config(updated)
            self._sync_normalization_provider_ui()
        return updated.effective_model

'''
replace_once(
    "src/tutor_assistant/ui/app.py",
    "    def _sync_normalization_controls(self) -> None:\n",
    provider_methods + "    def _sync_normalization_controls(self) -> None:\n",
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    "        can_start = bool(\n"
    "            self.lesson\n"
    "            and self.config.normalization.enabled\n"
    "            and self.segment_table.rowCount()\n"
    "            and not task_running\n"
    "        )\n"
    "        self.normalize_button.setEnabled(can_start)\n",
    "        provider_error = provider_configuration_error(self.config.normalization)\n"
    "        can_start = bool(\n"
    "            self.lesson\n"
    "            and self.config.normalization.enabled\n"
    "            and self.segment_table.rowCount()\n"
    "            and not task_running\n"
    "            and not provider_error\n"
    "        )\n"
    "        self.normalization_provider.setEnabled(not task_running)\n"
    "        self.normalize_button.setEnabled(can_start)\n"
    "        self.normalize_button.setToolTip(provider_error or \"Запустить LLM-фильтрацию\")\n",
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    "        if self.config.normalization.provider == \"ollama\" and (\n"
    "            self.transcription_worker.busy or self.transcription_queue.active\n"
    "        ):\n",
    "        provider = self._selected_normalization_provider()\n"
    "        configuration_error = provider_configuration_error(self.config.normalization)\n"
    "        if configuration_error:\n"
    "            QMessageBox.warning(self, \"LLM-фильтрация\", configuration_error)\n"
    "            return\n"
    "        if provider == \"ollama\" and (\n"
    "            self.transcription_worker.busy or self.transcription_queue.active\n"
    "        ):\n",
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    "        model = self.normalization_model.currentText().strip()\n"
    "        if not model:\n"
    "            QMessageBox.warning(\n"
    "                self,\n"
    "                \"LLM-фильтрация\",\n"
    "                \"Укажите модель\",\n"
    "            )\n"
    "            return\n",
    "        try:\n"
    "            model = self._persist_selected_normalization_model()\n"
    "        except Exception as exc:\n"
    "            QMessageBox.warning(self, \"LLM-фильтрация\", str(exc))\n"
    "            return\n",
)

replace_once(
    "src/tutor_assistant/ui/app.py",
    "            f\"Фильтрую учебное содержание · {self.config.normalization.provider} · {model}\",\n",
    "            f\"Фильтрую учебное содержание · {provider_label(provider)} · {model}\",\n",
)

replace_once(
    "README.md",
    "Старые команды `normalize` и `normalization-doctor`, а также внутреннее имя\n"
    "пакета `normalization` сохранены для обратной совместимости.\n",
    "В GUI провайдер выбирается непосредственно над редактором транскрипта: \n"
    "**«Локальная LLM (Ollama)»** или **«Yandex AI Studio»**. При первом выборе Yandex \n"
    "приложение запрашивает `folder_id` и явное согласие на передачу текста в облако. \n"
    "API-ключ читается только из переменной окружения и в YAML не сохраняется.\n\n"
    "Старые команды `normalize` и `normalization-doctor`, а также внутреннее имя\n"
    "пакета `normalization` сохранены для обратной совместимости.\n",
)
