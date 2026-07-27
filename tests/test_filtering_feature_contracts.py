from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

import tutor_assistant.normalization as normalization_package
from tutor_assistant.config import NormalizationConfig
from tutor_assistant.normalization import (
    EducationalContentFilterService,
    FilteredTranscript,
    SubjectProfileName,
    resolve_subject_profile,
)
from tutor_assistant.normalization.models import NormalizationChunkRequest
from tutor_assistant.normalization.ollama_client import OllamaClient
from tutor_assistant.normalization.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    system_prompt,
    user_prompt,
)
from tutor_assistant.normalization.service import NormalizationService
from tutor_assistant.normalization.yandex_client import YandexAIStudioClient
from tutor_assistant.ui import app as ui_app
from tutor_assistant.ui.normalization_provider import (
    provider_label,
    provider_models,
    select_provider_config,
    with_provider_model,
)


class _JsonResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def json(self) -> dict[str, object]:
        return self.payload


def _physics_request() -> NormalizationChunkRequest:
    profile = resolve_subject_profile("ЕГЭ физика")
    return NormalizationChunkRequest(
        lesson_id="physics-contract",
        prompt_version=profile.prompt_version,
        mode="filter_only",
        lesson_subject="ЕГЭ физика",
        subject_profile=profile.name.value,
        segments=[
            {
                "source_segment_id": 1,
                "speaker": "П",
                "text": "Импульс тела p = mv, масса равна 2 кг.",
            }
        ],
    )


def test_filtering_public_contracts_import_cleanly() -> None:
    assert EducationalContentFilterService is NormalizationService
    assert FilteredTranscript.__name__ == "NormalizedTranscript"
    assert SubjectProfileName.PHYSICS.value == "physics"
    assert resolve_subject_profile("органическая химия").name is SubjectProfileName.CHEMISTRY
    assert callable(system_prompt)
    assert callable(user_prompt)
    assert PROMPT_VERSION == "educational-content-filter.mathematics.v2"
    assert SYSTEM_PROMPT == system_prompt(SubjectProfileName.MATHEMATICS)


def test_public_compatibility_aliases_are_declared_once() -> None:
    source = inspect.getsource(normalization_package)
    assert source.count("EducationalContentFilterService = NormalizationService") == 1
    assert source.count("FilteredTranscript = NormalizedTranscript") == 1


@pytest.mark.parametrize(
    ("subject", "profile_name", "version", "marker"),
    (
        (
            "ЕГЭ математика",
            SubjectProfileName.MATHEMATICS,
            "educational-content-filter.mathematics.v2",
            "логариф",
        ),
        (
            "ЕГЭ физика",
            SubjectProfileName.PHYSICS,
            "educational-content-filter.physics.v1",
            "импульс",
        ),
        (
            "органическая химия",
            SubjectProfileName.CHEMISTRY,
            "educational-content-filter.chemistry.v1",
            "алкан",
        ),
        (
            "история",
            SubjectProfileName.GENERIC,
            "educational-content-filter.generic.v1",
            "общий учебный профиль",
        ),
    ),
)
def test_subject_prompt_contracts(
    subject: str,
    profile_name: SubjectProfileName,
    version: str,
    marker: str,
) -> None:
    profile = resolve_subject_profile(subject)
    prompt = system_prompt(profile.name)

    assert profile.name is profile_name
    assert profile.prompt_version == version
    assert version in prompt
    assert marker in prompt.casefold()
    assert "Разрешено только удаление фрагментов" in prompt
    assert "JSON" in prompt


def test_user_prompt_contains_raw_subject_and_resolved_profile() -> None:
    prompt = user_prompt(_physics_request())

    assert "Предмет занятия: ЕГЭ физика" in prompt
    assert "Физика (physics)" in prompt
    assert "Импульс тела p = mv" in prompt


def test_ollama_payload_uses_subject_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OllamaClient(NormalizationConfig(), model="qwen3:8b")
    captured: dict[str, object] = {}

    def fake_request(
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> _JsonResponse:
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return _JsonResponse({"message": {"content": "[П] Импульс тела p = mv, масса равна 2 кг."}})

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.normalize_chunk(_physics_request())
    payload = captured["payload"]

    assert result.startswith("[П] Импульс")
    assert isinstance(payload, dict)
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert "Предметный профиль: Физика" in messages[0]["content"]
    assert "Предмет занятия: ЕГЭ физика" in messages[1]["content"]


def test_yandex_payload_uses_subject_prompt_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = NormalizationConfig(
        provider="yandex_ai_studio",
        allow_cloud_processing=True,
        yandex_folder_id="folder-id",
        yandex_model="yandexgpt-lite",
    )
    client = YandexAIStudioClient(config)
    captured: dict[str, object] = {}
    monkeypatch.setenv(config.yandex_api_key_env, "contract-secret")

    def fake_request(
        payload: dict[str, object],
        **_kwargs: object,
    ) -> _JsonResponse:
        captured["payload"] = payload
        return _JsonResponse({"output_text": "[П] Импульс тела p = mv, масса равна 2 кг."})

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.normalize_chunk(_physics_request())
    payload = captured["payload"]

    assert result.startswith("[П] Импульс")
    assert isinstance(payload, dict)
    assert "Предметный профиль: Физика" in str(payload["input"])
    assert "Предмет занятия: ЕГЭ физика" in str(payload["input"])
    assert "contract-secret" not in repr(payload)


@pytest.mark.parametrize("provider", ("ollama", "yandex_ai_studio"))
def test_provider_doctor_uses_explicit_subject_contract(
    provider: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, NormalizationChunkRequest] = {}

    if provider == "ollama":
        client = OllamaClient(NormalizationConfig(), model="qwen3:8b")
        monkeypatch.setattr(client, "version", lambda: "test")
        monkeypatch.setattr(client, "check_available", lambda model=None: None)
    else:
        config = NormalizationConfig(
            provider="yandex_ai_studio",
            allow_cloud_processing=True,
            yandex_folder_id="folder-id",
        )
        client = YandexAIStudioClient(config)
        monkeypatch.setattr(client, "check_available", lambda model=None: None)

    def fake_normalize(request: NormalizationChunkRequest, **_kwargs: object) -> str:
        captured["request"] = request
        return "[П] Решаем уравнение x + 2 = 5."

    monkeypatch.setattr(client, "normalize_chunk", fake_normalize)

    diagnostics = client.diagnose()
    request = captured["request"]

    assert diagnostics.errors == []
    assert diagnostics.plain_text_valid is True
    assert request.lesson_subject == "mathematics"
    assert request.subject_profile == "mathematics"
    assert request.prompt_version == "educational-content-filter.mathematics.v2"


def test_gui_transcript_tab_does_not_shadow_provider_label() -> None:
    source = textwrap.dedent(inspect.getsource(ui_app.MainWindow._transcript_tab))
    tree = ast.parse(source)
    assigned_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "provider_label" not in assigned_names
    assert "provider_label" in called_names
    assert "ollama" in source
    assert "yandex_ai_studio" in source


def test_gui_start_persists_model_and_uses_display_label() -> None:
    source = inspect.getsource(ui_app.MainWindow.normalize_current_transcript)

    assert "_persist_selected_normalization_model" in source
    assert "provider_label(provider)" in source
    assert "provider_configuration_error" in source


def test_provider_models_remain_independent_across_switches() -> None:
    local = with_provider_model(NormalizationConfig(), "ollama", "qwen3:14b")
    cloud = select_provider_config(
        local,
        "yandex_ai_studio",
        folder_id="folder-id",
        allow_cloud_processing=True,
    )
    cloud = with_provider_model(cloud, "yandex_ai_studio", "yandexgpt")
    restored_local = select_provider_config(cloud, "ollama")

    assert provider_label("ollama") == "Локальная LLM (Ollama)"
    assert provider_models("yandex_ai_studio") == ("yandexgpt-lite", "yandexgpt")
    assert cloud.yandex_model == "yandexgpt"
    assert restored_local.model == "qwen3:14b"
    assert restored_local.effective_model == "qwen3:14b"
