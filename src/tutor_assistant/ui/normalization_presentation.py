from __future__ import annotations

from dataclasses import dataclass

from ..application.normalization import (
    NormalizationLifecycleState,
    NormalizationProgressSnapshot,
)
from ..normalization.models import NormalizationExecution, NormalizationRunStatus


@dataclass(frozen=True, slots=True)
class NormalizationControlContext:
    lifecycle_state: NormalizationLifecycleState
    has_lesson: bool
    enabled: bool
    has_segments: bool
    provider_error: str | None
    run_status: NormalizationRunStatus | None
    artifact_ready: bool
    review_candidate_chunks: int = 0
    fallback_chunks: int = 0
    warning_count: int = 0
    progress: NormalizationProgressSnapshot | None = None


@dataclass(frozen=True, slots=True)
class NormalizationPrimaryActionPresentation:
    action: str
    text: str
    enabled: bool
    kind: str | None = None
    visible: bool = True


@dataclass(frozen=True, slots=True)
class NormalizationProcessPresentation:
    title: str
    detail: str
    tone: str = "neutral"
    show_progress: bool = False


@dataclass(frozen=True, slots=True)
class NormalizationMenuPresentation:
    restart: bool
    open_artifact: bool
    show_warnings: bool
    reject: bool


@dataclass(frozen=True, slots=True)
class NormalizationControlsPresentation:
    provider_enabled: bool
    settings_enabled: bool
    primary: NormalizationPrimaryActionPresentation
    review_visible: bool
    review_enabled: bool
    review_text: str
    menu: NormalizationMenuPresentation
    process: NormalizationProcessPresentation


@dataclass(frozen=True, slots=True)
class NormalizationReadyPresentation:
    preview_summary: str
    process_title: str
    process_detail: str
    process_tone: str
    status_text: str
    status_tone: str


@dataclass(frozen=True, slots=True)
class NormalizationFailurePresentation:
    message: str
    process_title: str = "Фильтрация завершилась ошибкой"
    status_text: str = "Ошибка LLM-фильтрации"
    tone: str = "error"


def normalization_progress_detail(progress: NormalizationProgressSnapshot | None) -> str:
    if progress is None:
        return "Подготовка блоков и запуск первого запроса к модели."
    current = (
        f"Блок {progress.current_chunk + 1} из {progress.total_chunks}"
        if progress.current_chunk is not None and progress.total_chunks
        else "Подготовка блоков"
    )
    attempt = (
        f" · попытка {progress.current_attempt}"
        if progress.current_attempt is not None
        else ""
    )
    return (
        f"{current} · готово {progress.completed_chunks} · "
        f"восстановлено {progress.reused_chunks} · "
        f"запросов {progress.provider_requests}{attempt}"
    )


def build_normalization_controls(
    context: NormalizationControlContext,
) -> NormalizationControlsPresentation:
    running = context.lifecycle_state != NormalizationLifecycleState.IDLE
    can_start = bool(
        context.has_lesson
        and context.enabled
        and context.has_segments
        and not running
        and not context.provider_error
    )
    reject_enabled = context.run_status in {
        NormalizationRunStatus.PENDING,
        NormalizationRunStatus.RUNNING,
        NormalizationRunStatus.REVIEW_REQUIRED,
        NormalizationRunStatus.FAILED,
    }
    menu = NormalizationMenuPresentation(
        restart=can_start and context.run_status is not None,
        open_artifact=context.artifact_ready,
        show_warnings=context.artifact_ready,
        reject=reject_enabled,
    )

    if context.lifecycle_state == NormalizationLifecycleState.CANCELLING:
        return NormalizationControlsPresentation(
            provider_enabled=False,
            settings_enabled=False,
            primary=NormalizationPrimaryActionPresentation(
                "cancel",
                "Отменить",
                False,
                "danger",
            ),
            review_visible=False,
            review_enabled=False,
            review_text="Проверить результат перед применением",
            menu=menu,
            process=NormalizationProcessPresentation(
                "Отмена фильтрации…",
                "Текущий запрос будет корректно завершён или помечен как неопределённый.",
                "warning",
                True,
            ),
        )

    if context.lifecycle_state == NormalizationLifecycleState.RUNNING:
        return NormalizationControlsPresentation(
            provider_enabled=False,
            settings_enabled=False,
            primary=NormalizationPrimaryActionPresentation(
                "cancel",
                "Отменить",
                True,
                "danger",
            ),
            review_visible=False,
            review_enabled=False,
            review_text="Проверить результат перед применением",
            menu=menu,
            process=NormalizationProcessPresentation(
                "LLM-фильтрация выполняется",
                normalization_progress_detail(context.progress),
                "working",
                True,
            ),
        )

    if context.provider_error:
        primary = NormalizationPrimaryActionPresentation(
            "settings",
            "Настроить LLM",
            True,
            "primary",
        )
        process = NormalizationProcessPresentation(
            "LLM-фильтр требует настройки",
            context.provider_error,
            "warning",
        )
    elif not context.has_lesson:
        primary = NormalizationPrimaryActionPresentation(
            "start",
            "Запустить фильтрацию",
            False,
        )
        process = NormalizationProcessPresentation(
            "LLM-фильтрация недоступна",
            "Сначала откройте транскрипт занятия.",
        )
    elif not context.has_segments:
        primary = NormalizationPrimaryActionPresentation(
            "start",
            "Запустить фильтрацию",
            False,
        )
        process = NormalizationProcessPresentation(
            "Нет сегментов для фильтрации",
            "Дождитесь завершения транскрибации или загрузите сегменты занятия.",
            "warning",
        )
    elif (
        context.run_status == NormalizationRunStatus.REVIEW_REQUIRED
        and context.artifact_ready
    ):
        warning = bool(
            context.review_candidate_chunks
            or context.fallback_chunks
            or context.warning_count
        )
        return NormalizationControlsPresentation(
            provider_enabled=True,
            settings_enabled=True,
            primary=NormalizationPrimaryActionPresentation(
                "start",
                "Запустить фильтрацию",
                False,
                visible=False,
            ),
            review_visible=True,
            review_enabled=True,
            review_text="Проверить результат перед применением",
            menu=menu,
            process=NormalizationProcessPresentation(
                "Фильтрация завершена · требуется проверка",
                (
                    f"Кандидатов модели: {context.review_candidate_chunks} · "
                    f"fallback-блоков: {context.fallback_chunks} · "
                    f"предупреждений: {context.warning_count}. "
                    "Результат не будет применён без вашего подтверждения."
                ),
                "warning" if warning else "success",
            ),
        )
    elif context.run_status == NormalizationRunStatus.APPROVED and context.artifact_ready:
        primary = NormalizationPrimaryActionPresentation(
            "review",
            "Открыть результат",
            True,
            "ghost",
        )
        process = NormalizationProcessPresentation(
            "Результат фильтрации применён",
            "Новая ревизия транскрипта создана и готова к дальнейшей работе.",
            "success",
        )
    elif context.run_status == NormalizationRunStatus.FAILED:
        primary = NormalizationPrimaryActionPresentation(
            "retry",
            "Повторить",
            can_start,
        )
        process = NormalizationProcessPresentation(
            "Фильтрация завершилась ошибкой",
            "Проверьте доступность провайдера и повторите запуск.",
            "error",
        )
    elif context.run_status == NormalizationRunStatus.CANCELLED:
        primary = NormalizationPrimaryActionPresentation(
            "retry",
            "Запустить заново",
            can_start,
        )
        process = NormalizationProcessPresentation(
            "Фильтрация отменена",
            "Можно начать новый запуск с текущими настройками.",
            "warning",
        )
    else:
        primary = NormalizationPrimaryActionPresentation(
            "start",
            "Запустить фильтрацию",
            can_start,
        )
        process = NormalizationProcessPresentation(
            "LLM-фильтрация не запускалась",
            "Проверьте транскрипт и запустите фильтрацию, когда будете готовы.",
        )

    return NormalizationControlsPresentation(
        provider_enabled=True,
        settings_enabled=True,
        primary=primary,
        review_visible=False,
        review_enabled=False,
        review_text="Проверить результат перед применением",
        menu=menu,
        process=process,
    )


def build_normalization_ready_presentation(
    result: NormalizationExecution,
) -> NormalizationReadyPresentation:
    statistics = result.transcript.statistics
    quality = result.transcript.quality
    warnings = len(quality.warnings)
    fallback_chunks = statistics.source_fallback_chunks
    preview_summary = (
        f"Сохранено {statistics.retained_ratio * 100:.1f}% текста · "
        f"fallback-блоков: {fallback_chunks} · "
        f"запросов к модели: {statistics.provider_requests}"
    )
    process_detail = (
        f"Fallback-блоков: {fallback_chunks} · предупреждений: {warnings}. "
        "Проверьте результат перед применением."
    )
    prefix = (
        "LLM-фильтрация завершена с замечаниями · "
        f"исходный текст использован в блоках: {fallback_chunks} · "
        if fallback_chunks
        else "LLM-фильтрация готова · "
    )
    status_text = (
        prefix
        + f"сохранено {statistics.retained_ratio * 100:.1f}%"
        + f" · восстановлено блоков: {statistics.reused_chunks}"
        + (f" · предупреждений: {warnings}" if warnings else "")
    )
    tone = "warning" if quality.requires_manual_attention else "success"
    return NormalizationReadyPresentation(
        preview_summary=preview_summary,
        process_title="Фильтрация завершена · требуется проверка",
        process_detail=process_detail,
        process_tone=tone,
        status_text=status_text,
        status_tone=tone,
    )


def build_normalization_failure_presentation(details: str) -> NormalizationFailurePresentation:
    lines = [line.strip() for line in details.splitlines() if line.strip()]
    message = lines[-1] if lines else "Неизвестная ошибка нормализации"
    return NormalizationFailurePresentation(message=message)
