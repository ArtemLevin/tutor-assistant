from __future__ import annotations

from types import SimpleNamespace

from tutor_assistant.application.normalization import (
    NormalizationLifecycleState,
    NormalizationProgressSnapshot,
)
from tutor_assistant.normalization.models import NormalizationRunStatus
from tutor_assistant.ui.normalization_presentation import (
    NormalizationControlContext,
    build_normalization_controls,
    build_normalization_failure_presentation,
    build_normalization_ready_presentation,
    normalization_progress_detail,
)


def context(**overrides):
    values = {
        "lifecycle_state": NormalizationLifecycleState.IDLE,
        "has_lesson": True,
        "enabled": True,
        "has_segments": True,
        "provider_error": None,
        "run_status": None,
        "artifact_ready": False,
    }
    values.update(overrides)
    return NormalizationControlContext(**values)


def test_idle_controls_offer_manual_start() -> None:
    view = build_normalization_controls(context())

    assert view.primary.action == "start"
    assert view.primary.text == "Запустить фильтрацию"
    assert view.primary.enabled is True
    assert view.process.title == "LLM-фильтрация не запускалась"
    assert view.provider_enabled is True


def test_provider_error_routes_primary_action_to_settings() -> None:
    view = build_normalization_controls(context(provider_error="Нет API-ключа"))

    assert view.primary.action == "settings"
    assert view.primary.enabled is True
    assert view.process.title == "LLM-фильтр требует настройки"
    assert view.process.detail == "Нет API-ключа"
    assert view.process.tone == "warning"


def test_running_controls_use_progress_snapshot_without_widget_reads() -> None:
    progress = NormalizationProgressSnapshot(
        current_chunk=1,
        total_chunks=4,
        completed_chunks=1,
        reused_chunks=2,
        provider_requests=3,
        current_attempt=2,
        state="running",
    )
    view = build_normalization_controls(
        context(
            lifecycle_state=NormalizationLifecycleState.RUNNING,
            progress=progress,
        )
    )

    assert view.primary.action == "cancel"
    assert view.primary.kind == "danger"
    assert view.provider_enabled is False
    assert view.process.show_progress is True
    assert view.process.progress_total == 4
    assert view.process.progress_completed == 1
    assert view.process.detail == (
        "Блок 2 из 4 · готово 1 · восстановлено 2 · запросов 3 · попытка 2"
    )


def test_cancelling_state_disables_duplicate_cancel() -> None:
    view = build_normalization_controls(
        context(lifecycle_state=NormalizationLifecycleState.CANCELLING)
    )

    assert view.primary.action == "cancel"
    assert view.primary.enabled is False
    assert view.process.title == "Отмена фильтрации…"
    assert view.process.tone == "warning"


def test_review_required_switches_to_explicit_review_action() -> None:
    view = build_normalization_controls(
        context(
            run_status=NormalizationRunStatus.REVIEW_REQUIRED,
            artifact_ready=True,
            review_candidate_chunks=2,
            fallback_chunks=1,
            warning_count=3,
        )
    )

    assert view.primary.visible is False
    assert view.review_visible is True
    assert view.review_enabled is True
    assert view.process.tone == "warning"
    assert "Кандидатов модели: 2" in view.process.detail
    assert view.menu.open_artifact is True
    assert view.menu.reject is True


def test_approved_failed_and_cancelled_runs_preserve_existing_actions() -> None:
    approved = build_normalization_controls(
        context(run_status=NormalizationRunStatus.APPROVED, artifact_ready=True)
    )
    failed = build_normalization_controls(
        context(run_status=NormalizationRunStatus.FAILED)
    )
    cancelled = build_normalization_controls(
        context(run_status=NormalizationRunStatus.CANCELLED)
    )

    assert approved.primary.action == "review"
    assert approved.primary.text == "Открыть результат"
    assert failed.primary.action == "retry"
    assert failed.primary.text == "Повторить"
    assert cancelled.primary.action == "retry"
    assert cancelled.primary.text == "Запустить заново"


def test_progress_without_chunks_uses_preparation_copy() -> None:
    progress = NormalizationProgressSnapshot(
        current_chunk=None,
        total_chunks=0,
        completed_chunks=0,
        reused_chunks=0,
        provider_requests=0,
        current_attempt=None,
        state="preparing",
    )

    assert normalization_progress_detail(progress) == (
        "Подготовка блоков · готово 0 · восстановлено 0 · запросов 0"
    )


def test_ready_presentation_centralizes_result_copy_and_tone() -> None:
    result = SimpleNamespace(
        transcript=SimpleNamespace(
            statistics=SimpleNamespace(
                retained_ratio=0.875,
                source_fallback_chunks=2,
                provider_requests=5,
                reused_chunks=1,
            ),
            quality=SimpleNamespace(
                warnings=["a", "b"],
                requires_manual_attention=True,
            ),
        )
    )

    view = build_normalization_ready_presentation(result)

    assert view.preview_summary == (
        "Сохранено 87.5% текста · fallback-блоков: 2 · запросов к модели: 5"
    )
    assert view.process_tone == "warning"
    assert "предупреждений: 2" in view.process_detail
    assert "исходный текст использован в блоках: 2" in view.status_text


def test_failure_presentation_extracts_last_meaningful_line() -> None:
    view = build_normalization_failure_presentation(
        "Traceback...\nRuntimeError: provider unavailable\n"
    )

    assert view.message == "RuntimeError: provider unavailable"
    assert view.tone == "error"
