from pathlib import Path


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


readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
readme = replace_once(
    readme,
    "- Qt-free `NormalizationCoordinator` для manual/auto scheduling, lifecycle, cancellation/progress и resume decisions; `normalization_presentation` централизует actions/process/result state, а explicit Yandex consent остаётся в UI adapter.\n",
    "- Qt-free `NormalizationCoordinator` для manual/auto scheduling, lifecycle, cancellation/progress и resume decisions; `normalization_presentation` централизует actions/process/result state, а explicit Yandex consent остаётся в UI adapter.\n"
    "- Qt-free `LatexMonitorCoordinator` для enable/disable, manual/periodic scan eligibility и single-flight state; `latex_monitor_presentation` централизует no-update/success/failure UI state, а `RemoteLatexService` остаётся infrastructure concern.\n",
    "README architecture bullet",
)
readme = replace_once(
    readme,
    "**Wave 2 завершён, Wave 3 / Slices 13–14 также завершены.** Следующий архитектурный шаг — **Wave 3 / Slice 15: LaTeX monitor UI orchestration extraction**. Цель — отделить polling/single-flight/outcome state удалённого LaTeX monitor от base Qt window, сохранив `RemoteLatexService`, pipeline persistence и remote branch semantics. Детали описаны в [`PLAN.md`](PLAN.md).",
    "**Wave 2 завершён, Wave 3 / Slices 13–15 также завершены.** Следующий архитектурный шаг — **Wave 3 / Slice 16: Application shutdown coordinator**. Цель — вынести решение о безопасном закрытии, drain barriers и ready transition из base Qt window, сохранив recording finalize safety, normalization cancellation и persisted transcription queue semantics. Детали описаны в [`PLAN.md`](PLAN.md).",
    "README next step",
)
readme = replace_once(
    readme,
    "На текущем этапе Wave 2 и Wave 3 / Slices 13–14 завершены. Приоритет Wave 3 — декомпозиция LaTeX monitor orchestration, затем shutdown coordination и parallel-review synchronization.",
    "На текущем этапе Wave 2 и Wave 3 / Slices 13–15 завершены. Приоритет Wave 3 — application shutdown coordination, затем teacher cockpit / parallel-review synchronization.",
    "README roadmap footer",
)
readme_path.write_text(readme, encoding="utf-8")

plan_path = Path("PLAN.md")
plan = plan_path.read_text(encoding="utf-8")
plan = replace_once(
    plan,
    "Wave 2 архитектурной стабилизации production-контура записи завершён. В Wave 3 завершены Slices 13–14: orchestration очереди транскрибации и LLM normalization отделены от базового Qt-окна. Следующий фокус — LaTeX monitor UI orchestration.",
    "Wave 2 архитектурной стабилизации production-контура записи завершён. В Wave 3 завершены Slices 13–15: orchestration очереди транскрибации, LLM normalization и LaTeX monitor отделены от базового Qt-окна. Следующий фокус — application shutdown coordination.",
    "PLAN current state",
)
section = '''## 6. Завершённый шаг — Wave 3 / Slice 15

### LaTeX monitor UI orchestration extraction

**Цель:** отделить polling/scan lifecycle и presentation автоматического LaTeX monitor от `ui/app.py`, сохранив `RemoteLatexService`, pipeline persistence, retry/fix-request semantics и ручную локальную компиляцию как отдельный workflow.

Реализовано:

- новый Qt-free `application/latex_monitor.py` с `LatexMonitorCoordinator`;
- coordinator владеет enabled/disabled state, manual/periodic/enable scan eligibility и single-flight guard;
- manual «Проверить сейчас» остаётся доступным при выключенном auto-monitor, тогда как periodic/enable triggers требуют включённого monitor;
- `worker.purpose == "latex-monitor"` больше не используется как source of truth для lifecycle;
- новый Qt-free `ui/latex_monitor_presentation.py` централизует toggle/scanning/no-update/success/compile-failure/worker-error presentation state;
- `QTimer`, generic `Worker`, `QMessageBox` и widget mutation остаются transport/presentation adapters;
- concrete `RemoteLatexService` остаётся infrastructure concern; `content_service.activity("latex-monitor")`, обход lessons, `is_ready()` и `compile_lesson()` сохранены без изменения remote branch protocol;
- `_remote_compilation_ready` по-прежнему выполняет `pipeline.save_state(..., force_status=True)` перед presentation результата;
- compile-failure по-прежнему сообщает об `reports/latex/latex_fix_request.md`, а success сохраняет сообщение о PDF и branch;
- generic `_operation_failed` больше не владеет LaTeX-monitor state; worker error имеет отдельный typed presentation path;
- `compile_local_tex()` и локальный `LatexCompiler` workflow не изменялись;
- временная migration infrastructure удалена из итогового product diff.

Тестовый контур покрывает disabled/enabled lifecycle, manual и periodic triggers, single-flight, disable-during-scan, idempotent finish, no-update, success, compile-failure, worker-error, exact legacy copy/tone и architecture boundaries.

Первый PR CI обнаружил только `Ruff I001` в import block `ui/app.py`; runtime semantics не менялись. После исправления import ordering свежий exact head `49cf6930f1c6afa70d518620825b84acb6bc5696` прошёл Privacy History Gate и scaling 100/125/150/200%; перед merge получены независимые full-suite SUCCESS на Python 3.12 и 3.13. PR #97 squash-merged в `main` как `c7bb109db8cc93373f9ece2a9f9615584c5fd64b`.

## 7. Следующий шаг — Wave 3 / Slice 16

### Application shutdown coordinator

**Цель:** вынести decision/state безопасного завершения приложения из `closeEvent()` / `_maybe_finish_shutdown()` за Qt-free application boundary, сохранив audio-first recording finalize safety, cancellation активной normalization, корректное завершение transcription worker и persisted pending jobs.

План Slice 16:

1. проинвентаризировать `closeEvent`, `_maybe_finish_shutdown`, `_shutdown_requested/_shutdown_ready`, остановку timers, normalization cancellation и `TranscriptionWorker.shutdown()/wait()`;
2. ввести Qt-free shutdown coordinator/state model с явными фазами вроде idle/requested/draining/ready и typed close decisions/actions;
3. отделить policy от Qt: `QCloseEvent`, `QMessageBox`, `QTimer.singleShot`, widget disabling и thread wait остаются UI/transport adapters;
4. сохранить быстрый путь закрытия без активной записи/фоновых работ, включая корректный shutdown/wait transcription thread;
5. при подтверждённом busy shutdown сохранить normalization cancellation, stop всех runtime timers, запрет нового старта и безопасный `_stop_recording_async(...)` для активной записи;
6. не очищать persisted transcription queue: ожидающие jobs должны по-прежнему продолжаться при следующем запуске;
7. учитывать recording finalize, generic workers и transcription thread как независимые drain barriers; `ready` разрешается только после снятия всех барьеров;
8. сделать transition в ready идемпотентным и исключить повторные prompt/stop/cancel side effects при повторном `closeEvent`;
9. добавить pure unit tests для immediate close, busy→prompt, user cancel, confirmed drain, recording barrier, worker/transcription barriers, normalization cancellation action и final ready transition;
10. добавить architecture gates против возврата shutdown policy в `closeEvent`, не смешивая Slice 16 с teacher cockpit / parallel-review synchronization.

### Definition of Done Slice 16

- shutdown lifecycle decisions тестируются без PySide6;
- `QCloseEvent` и dialogs только исполняют typed decisions coordinator-а;
- recording finalize нельзя обойти при закрытии;
- active normalization получает cancellation request ровно один раз на shutdown lifecycle;
- pending transcription jobs не теряются и не удаляются;
- application достигает ready только после recording/worker/transcription drain;
- повторный close во время draining не дублирует destructive side effects;
- Windows CI, privacy gate и scaling matrix зелёные;
- минимум два независимых full-suite success;
- PR squash-merged в `main`.

### Последующие Wave 3 slices

После Slice 16:

1. teacher cockpit / parallel review synchronization.

Каждый этап следует тому же правилу: сначала выделяется реальная policy/orchestration в Qt-free boundary, затем production path переключается на неё, после чего dead legacy-код физически удаляется.

'''
plan = replace_between(
    plan,
    "## 6. Следующий шаг — Wave 3 / Slice 15\n",
    "## 7. Инварианты разработки\n",
    section,
    "PLAN Slice15/Slice16 section",
)
plan = replace_once(
    plan,
    "## 7. Инварианты разработки",
    "## 8. Инварианты разработки",
    "PLAN invariants heading",
)
plan = replace_once(
    plan,
    "## 8. Рабочий порядок для следующих slices",
    "## 9. Рабочий порядок для следующих slices",
    "PLAN workflow heading",
)
plan_path.write_text(plan, encoding="utf-8")
