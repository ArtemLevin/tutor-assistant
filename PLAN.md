# Tutor Assistant — Development Plan

**Актуальность:** 18 августа 2026 года  
**Текущая версия:** `0.22.1`  
**Основная ветка:** `main`

## 1. Текущее состояние

Wave 2 архитектурной стабилизации production-контура записи завершён. В Wave 3 завершены Slices 13–15: orchestration очереди транскрибации, LLM normalization и LaTeX monitor отделены от базового Qt-окна. Следующий фокус — application shutdown coordination.

Production GUI запускается через:

```text
tutor-assistant-gui
→ tutor_assistant.ui.recording_recovery_app:main
```

Актуальная цепочка ответственности:

```text
Qt presentation / composition adapters
        ↓
application use cases + structural ports
        ↓
domain / pipeline services
        ↓
recording, persistence and external infrastructure
```

Базовый `ui/app.py` остаётся общим presentation shell и набором command ports. Он больше не должен владеть транзакциями старта/остановки/восстановления записи, preflight capture, concrete recorder construction, hardware discovery, health-policy interpretation или форматированием состояния recording panel.

## 2. Завершённая стабилизация

### P0 — надёжность записи и данных

Завершены:

- безопасный lifecycle фоновых Qt-задач;
- формальная recording state machine;
- идемпотентное восстановление незавершённой записи;
- SQLite concurrency/PRAGMA contracts;
- cancelled lessons;
- исправление CI-контрактов после введения recording state machine.

### Wave 2 — декомпозиция production recording path

Завершены следующие slices:

1. **Recording lifecycle controller** — Qt-free управление фазами recording workflow.
2. **StartRecordingUseCase** — создание занятия, lease, recorder start и rollback перенесены в application layer.
3. **Stop/Finalize** — безопасная остановка, finalize и recovery-required semantics вынесены из base UI.
4. **Recovery** — восстановление аудио и metadata orchestration перенесены в `RecoverRecordingUseCase`.
5. **Legacy recording cleanup** — физически удалены старые start/stop/recovery orchestration и callback bridges из `ui/app.py` и промежуточных MRO-слоёв.
6. **Audio Preflight boundary** — production diagnostic capture выполняется через `AudioPreflightUseCase` и typed result.
7. **Legacy Audio Preflight cleanup** — удалены dead capture/JSON/sleep callbacks из base UI.
8. **Recording runtime port** — concrete `DualRecorder` исключён из base UI; monitoring работает через `RecordingRuntimeRecorder`, `RecordingLevelsSnapshot` и `RecordingHealthSnapshot`.
9. **Audio device discovery boundary** — hardware discovery/resolution/probe вынесены за base UI; production adapter использует `RefreshAudioDevicesUseCase`, а stable microphone identity централизована в нейтральном resolver.
10. **Recording runtime health policy** — интерпретация stream errors, callback timeout, silence и dropped blocks вынесена из `_tick()` в Qt-free `RecordingHealthMonitor`; UI получает typed assessment и только отображает состояние/исполняет terminal stop action.
11. **Recording presentation extraction** — timer formatting, level normalization, health summary, warning/recovery presentation cues и канонические visual phases recording panel вынесены в Qt-free `ui/recording_presentation.py`; start/finalize adapters больше не форматируют recording-state label вручную.
12. **Production composition cleanup** — общий GUI bootstrap принимает explicit `window_type`; устранены module-global `base_app.MainWindow = MainWindow` rebinding во всех production adapters, при этом responsibility-bearing MRO и стабильный console entrypoint сохранены и закреплены architecture tests.

На момент завершения Slice 12 exact feature head прошёл lint/compile/import/contracts на Windows matrix Python 3.11–3.14; перед merge получены независимые полные успешные regression runs минимум на Python 3.12 и 3.14, а privacy gate и scaling matrix 100/125/150/200% были зелёными.

## 3. Завершённый шаг — Wave 2 / Slice 12

### Production composition cleanup

**Цель:** завершить recording-focused Wave 2 явным composition root без module-global подмены класса окна и без искусственного схлопывания слоёв, которые всё ещё несут самостоятельную ответственность.

Инвентаризация production MRO подтвердила, что все слои остаются содержательными:

```text
recording_recovery_app.MainWindow
→ recording_finalize_app.MainWindow
→ audio_resilient_app.MainWindow
→ transcript_publication_app.MainWindow
→ concurrent_app.MainWindow
→ ui.app.MainWindow
```

Ответственности остаются разделены:

- `concurrent_app` — background tasks и parallel-review coordination;
- `transcript_publication_app` — publication/cockpit presentation;
- `audio_resilient_app` — audio-device refresh, preflight и start recording;
- `recording_finalize_app` — stop/finalize и recovery-required outcome presentation;
- `recording_recovery_app` — discovery и восстановление незавершённых recording sessions.

Поэтому Slice 12 не удаляет эти классы. Вместо старого механизма:

```python
base_app.MainWindow = MainWindow
base_app.main()
```

общий bootstrap теперь принимает явный тип окна:

```python
def main(window_type: type[MainWindow] = MainWindow) -> None:
    ...
    window = window_type(config_path)
```

а каждый production adapter запускается через:

```python
base_app.main(MainWindow)
```

Console entrypoint остаётся прежним:

```text
tutor-assistant-gui = tutor_assistant.ui.recording_recovery_app:main
```

### Architecture gates Slice 12

`tests/test_production_composition.py` закрепляет:

- explicit `window_type` injection в общем bootstrap;
- отсутствие `base_app.MainWindow = MainWindow` во всех production composition modules;
- точный responsibility-bearing MRO;
- наличие собственной ответственности у каждого слоя;
- неизменность production console entrypoint;
- отсутствие временных migration files.

Первые CI-запуски выявили только `Ruff I001` в новом architecture test. Runtime diff не менялся; import formatting теста приведён к проектному стандарту, после чего свежий exact head прошёл ранние gates и требуемые full-suite проверки.

**Итог Wave 2:** recording lifecycle, start/stop/recovery, preflight, device discovery, runtime recorder contract, health policy, presentation mapping и production composition имеют явные границы и regression/architecture coverage. Дальнейшая декомпозиция должна идти уже по другим функциональным зонам `ui/app.py`.

## 4. Завершённый шаг — Wave 3 / Slice 13

### Transcription queue presentation/orchestration extraction

**Цель:** отделить управление локальной очередью транскрибации и её presentation state от базового Qt god-object, сохранив storage/retry/shutdown semantics и не меняя recording path.

Реализовано:

- новый Qt-free `application/transcription_queue.py` с `TranscriptionQueueCoordinator`;
- coordinator владеет restore/pump/retry/complete/fail/discard decisions поверх существующей `TranscriptionQueue` state machine;
- `TranscriptionQueueSnapshot` и entry snapshots дают UI immutable queue state без чтения widgets как source of truth;
- `ui/transcription_queue_presentation.py` централизует labels, row text/tooltips, counters и quick badge;
- `TranscriptionWorker` физически вынесен из `ui/app.py` в `ui/transcription_worker.py` как Qt transport adapter;
- `ui/app.py` делегирует restore/pump/retry/completion coordinator-у и только запускает worker/рендерит presentation;
- `concurrent_app.py` больше не дублирует Lesson transition, persistence и raw queue retry;
- normalization busy gates читают active transcription через coordinator, сохраняя CPU mutual exclusion Ollama ↔ Whisper;
- временная migration infrastructure удалена из итогового product diff.

Тестовый контур покрывает sequential pump, shutdown/normalization barriers, fail→next, retry и missing audio, persisted restore semantics, orphan recovery, snapshots, presentation formatting и architecture boundaries.

Финальный product diff: 9 файлов. Exact head прошёл Privacy History Gate и scaling 100/125/150/200%; перед merge получены независимые full-suite SUCCESS минимум на Python 3.12 и 3.14. PR #92 squash-merged в `main`.

## 5. Завершённый шаг — Wave 3 / Slice 14

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

## 6. Завершённый шаг — Wave 3 / Slice 15

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

## 8. Инварианты разработки

При дальнейших изменениях сохранять:

- **audio-first recovery:** ошибки metadata не должны мешать физическому восстановлению WAV;
- **no rollback of progressed Lesson status:** recovery не откатывает уже продвинувшееся занятие назад;
- **recording safety over convenience:** shutdown/cancellation не должны терять safety-critical finalize callbacks;
- **SQLite as source of truth** для локального архива и pipeline metadata;
- **local-first privacy:** аудио не отправляется во внешние сервисы;
- **explicit cloud consent** для Yandex AI Studio;
- **no concrete audio infrastructure in base UI**;
- **no Qt in application use cases**;
- **stable device identity** не должна зависеть только от transient PortAudio index;
- **presentation mapping не возвращается в application health policy**;
- любое изменение recording lifecycle сопровождается regression/architecture tests.

## 9. Рабочий порядок для следующих slices

Для каждого slice:

1. зафиксировать фактическую текущую зависимость по `main`;
2. определить минимальную архитектурную границу;
3. не смешивать соседние refactor-задачи в один PR;
4. сначала добавить/перенести contract и тесты;
5. физически удалить старую orchestration после переключения production path;
6. выполнить self-review diff;
7. прогнать Windows CI и policy gates;
8. получить минимум два независимых full-suite success для существенных orchestration/refactor changes;
9. squash merge с проверкой expected head SHA;
10. обновлять этот `PLAN.md`, когда завершённый slice меняет следующую архитектурную границу.