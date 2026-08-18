from __future__ import annotations

from pathlib import Path


PLAN = Path("PLAN.md")
README = Path("README.md")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"{label}: start marker not found")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:start_index] + replacement + text[end_index:]


plan = PLAN.read_text(encoding="utf-8")
section = '''## 5. Завершённый шаг — Wave 3 / Slice 14

### LLM normalization presentation/orchestration extraction

**Цель:** отделить scheduling, lifecycle и presentation state LLM-фильтрации от `ui/app.py`, сохранив manual review, cancellation/resume, explicit cloud consent и запрет одновременной CPU-heavy Ollama/Whisper обработки.

Реализовано:

- новый Qt-free `application/normalization.py` с `NormalizationCoordinator`;
- coordinator владеет manual-start barriers, lifecycle `idle/running/cancelling`, FIFO/dedup auto-run queue, progress snapshot и post-worker resume decision;
- manual-start policy сохраняет порядок проверок: lesson → provider configuration → Ollama/Whisper CPU barrier → active normalization → source segments;
- Yandex auto-normalization не отправляет текст автоматически: pending job остаётся в очереди и возвращает `WAITING_CLOUD_CONSENT` до ручного согласия преподавателя;
- `ui/normalization_presentation.py` централизует primary/review actions, menu state, process title/detail/tone, progress range/value и result/failure copy;
- `ui/app.py` теперь собирает context, запрашивает Yandex consent, запускает `NormalizationWorker` и рендерит typed presentation;
- `_pending_auto_normalizations` и `_retry_indeterminate_after_worker` удалены как UI-owned state;
- progress/cancel/resume/worker-finished callbacks делегируют state transitions coordinator-у;
- Ollama ↔ Whisper mutual exclusion продолжает использовать `TranscriptionQueueCoordinator.active` и `TranscriptionWorker.busy`;
- `NormalizationWorker`, `NormalizationService`, Ollama/Yandex providers, consent dialog и review dialog сохранены как transport/infrastructure/UI adapters.

Во время self-review до открытия PR выявлена и исправлена регрессия progress bar: первая версия typed presentation сохраняла текст прогресса, но не обновляла `range/value`. В presentation model добавлены `progress_total/progress_completed`, а adapter снова использует `TranscriptWorkspace.set_progress()`.

Тестовый контур покрывает manual start barriers, provider-specific CPU policy, auto FIFO/dedup, shutdown/transcription barriers, Yandex consent gate, cancellation, progress snapshots, resume-after-worker, controls/result/failure presentation и architecture boundaries.

Финальный product diff: 7 файлов; `ui/app.py` уменьшен на 149 строк net. Exact head `399f3c6a77fd4d3a9480f747562acc78d76f1b36` прошёл Privacy History Gate и scaling 100/125/150/200%; перед merge получены независимые full-suite SUCCESS на Python 3.13 и 3.14. PR #94 squash-merged в `main` как `c0c2cdf18330cf5cdf9039cbb60b044b5a27764c`.

## 6. Следующий шаг — Wave 3 / Slice 15

### LaTeX monitor UI orchestration extraction

**Цель:** отделить polling/scan/compile coordination удалённых LaTeX-веток от `ui/app.py`, сохранив current `RemoteLatexService`, pipeline persistence, retry/fix-request semantics и ручную локальную компиляцию как отдельный workflow.

План Slice 15:

1. проинвентаризировать `toggle_latex_monitor`, `scan_remote_latex`, `_remote_compilation_ready` и ветку `purpose == "latex-monitor"` в `_operation_failed`;
2. отделить policy monitor lifecycle от Qt timer/worker transport: enabled/disabled, scan-in-flight, idle/no-update, success/failure outcome должны стать typed Qt-free state;
3. ввести Qt-free application coordinator для решения `should_scan`, single-flight guard и обработки scan outcome, не создавая `RemoteLatexService` внутри application layer;
4. оставить `QTimer` и generic `Worker` в Qt adapter, а concrete `RemoteLatexService`/`LatexCompiler` — infrastructure concern;
5. вынести status/message/log/preview presentation mapping автоматического monitor path в typed presentation model, не смешивая с manual `compile_local_tex`;
6. сохранить `content_service.activity("latex-monitor")`, обход lessons, `RemoteLatexService.is_ready()` и `compile_lesson()` semantics без изменения remote branch protocol;
7. сохранить `pipeline.save_state(..., force_status=True)` после remote compilation и существующие success/fix-request outcomes;
8. добавить pure unit tests для enable/disable, single-flight, no-update, success, compile-failure и worker-error paths, плюс architecture gates против возврата raw monitor orchestration в base UI;
9. не смешивать Slice 15 с application shutdown coordinator или teacher cockpit synchronization.

### Definition of Done Slice 15

- monitor lifecycle/decision state тестируется без PySide6;
- Qt timer и worker остаются transport-only adapters;
- повторный poll не запускает второй scan, пока первый не завершён;
- no-update/success/failure outcomes имеют typed presentation state;
- remote LaTeX branch/compile/persistence semantics не изменены;
- manual local compilation остаётся отдельным workflow;
- recording/transcription/normalization boundaries остаются неизменными;
- Windows CI, privacy gate и scaling matrix зелёные;
- минимум два независимых full-suite success;
- PR squash-merged в `main`.

### Последующие Wave 3 slices

После Slice 15:

1. application shutdown coordinator;
2. teacher cockpit / parallel review synchronization.

Каждый этап следует тому же правилу: сначала выделяется реальная policy/orchestration в Qt-free boundary, затем production path переключается на неё, после чего dead legacy-код физически удаляется.

'''
plan = replace_between(
    plan,
    "## 5. Следующий шаг — Wave 3 / Slice 14\n",
    "## 6. Инварианты разработки\n",
    section + "## 7. Инварианты разработки\n",
    "slice14/15 roadmap",
)
plan = replace_once(plan, "## 7. Рабочий порядок для следующих slices\n", "## 8. Рабочий порядок для следующих slices\n", "work-order numbering")
PLAN.write_text(plan, encoding="utf-8")

readme = README.read_text(encoding="utf-8")
readme = replace_once(
    readme,
    "- Qt-free `TranscriptionQueueCoordinator` для restore/pump/retry/complete/fail decisions; queue UI получает typed snapshot, а `TranscriptionWorker` вынесен из `ui/app.py` в отдельный Qt transport adapter.\n\n"
    "**Wave 2 завершён, Wave 3 / Slice 13 также завершён.** Следующий архитектурный шаг — **Wave 3 / Slice 14: LLM normalization presentation/orchestration extraction**. Цель — отделить normalization scheduling, execution state и result transitions от base Qt window, сохранив explicit cloud consent, cancellation/resume и mutual exclusion с локальной Whisper-транскрибацией. Детали описаны в [`PLAN.md`](PLAN.md).",
    "- Qt-free `TranscriptionQueueCoordinator` для restore/pump/retry/complete/fail decisions; queue UI получает typed snapshot, а `TranscriptionWorker` вынесен из `ui/app.py` в отдельный Qt transport adapter.\n"
    "- Qt-free `NormalizationCoordinator` для manual/auto scheduling, lifecycle, cancellation/progress и resume decisions; `normalization_presentation` централизует actions/process/result state, а explicit Yandex consent остаётся в UI adapter.\n\n"
    "**Wave 2 завершён, Wave 3 / Slices 13–14 также завершены.** Следующий архитектурный шаг — **Wave 3 / Slice 15: LaTeX monitor UI orchestration extraction**. Цель — отделить polling/single-flight/outcome state удалённого LaTeX monitor от base Qt window, сохранив `RemoteLatexService`, pipeline persistence и remote branch semantics. Детали описаны в [`PLAN.md`](PLAN.md).",
    "README architecture status",
)
readme = replace_once(
    readme,
    "На текущем этапе Wave 2 и transcription-queue Slice 13 завершены. Приоритет Wave 3 — декомпозиция LLM normalization orchestration, затем LaTeX monitor, shutdown coordination и parallel-review synchronization.",
    "На текущем этапе Wave 2 и Wave 3 / Slices 13–14 завершены. Приоритет Wave 3 — декомпозиция LaTeX monitor orchestration, затем shutdown coordination и parallel-review synchronization.",
    "README roadmap tail",
)
README.write_text(readme, encoding="utf-8")
