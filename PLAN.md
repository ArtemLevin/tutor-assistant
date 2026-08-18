# Tutor Assistant — Development Plan

**Актуальность:** 18 августа 2026 года  
**Текущая версия:** `0.22.1`  
**Основная ветка:** `main`

## 1. Текущее состояние

Wave 2 архитектурной стабилизации production-контура записи завершён. В Wave 3 завершены Slices 13–16: orchestration очереди транскрибации, LLM normalization, LaTeX monitor и безопасного завершения приложения отделены от базового Qt-окна.

Следующий фокус — **Wave 3 / Slice 17: Teacher cockpit / parallel-review synchronization**.

Production GUI запускается через:

```text
tutor-assistant-gui
→ tutor_assistant.ui.recording_recovery_app:main
```

Текущая production composition после Slice 16:

```text
recording_recovery_app.MainWindow
→ recording_finalize_app.MainWindow
→ audio_resilient_app.MainWindow
→ transcript_publication_app.MainWindow
→ shutdown_app.MainWindow
→ concurrent_app.MainWindow
→ ui.app.MainWindow
```

Общая архитектурная граница:

```text
Qt presentation / production composition adapters
                ↓
application use cases + coordinators + structural ports
                ↓
domain / pipeline services
                ↓
recording, persistence and external infrastructure
```

`ui/app.py` остаётся общим presentation shell и набором command ports. Новая функциональная orchestration должна добавляться через typed Qt-free boundaries и production adapters, а не возвращаться в base god-object.

## 2. Завершённая стабилизация

### P0 — надёжность записи и данных

Завершены:

- безопасный lifecycle фоновых Qt-задач;
- формальная recording state machine;
- идемпотентное восстановление незавершённой записи;
- SQLite concurrency/PRAGMA contracts;
- cancelled lessons;
- CI-контракты для recording state machine.

### Wave 2 — production recording path

Завершены Slices 1–12:

1. `RecordingWorkflowController`;
2. `StartRecordingUseCase`;
3. application-owned stop/finalize;
4. `RecoverRecordingUseCase`;
5. legacy recording cleanup;
6. `AudioPreflightUseCase`;
7. legacy preflight cleanup;
8. `RecordingRuntimeRecorder` port;
9. audio-device discovery / stable identity boundary;
10. `RecordingHealthMonitor`;
11. `recording_presentation`;
12. explicit production composition root без `base_app.MainWindow` rebinding.

К завершению Wave 2 recording lifecycle, start/stop/recovery, preflight, device discovery, runtime health, presentation и production composition имеют отдельные regression/architecture contracts.

## 3. Wave 3 — завершённые slices

### Slice 13 — Transcription queue presentation/orchestration extraction

Реализовано:

- Qt-free `TranscriptionQueueCoordinator` для restore/pump/retry/complete/fail/discard decisions;
- immutable queue snapshots для UI;
- `transcription_queue_presentation`;
- `TranscriptionWorker` вынесен из `ui/app.py` в отдельный Qt transport adapter;
- raw queue mutation удалена из production UI orchestration;
- сохранены persisted restore, retry и Ollama ↔ Whisper mutual exclusion semantics.

Product PR #92 squash-merged в `main`.

### Slice 14 — LLM normalization presentation/orchestration extraction

Реализовано:

- Qt-free `NormalizationCoordinator`;
- manual-start barriers и provider-specific CPU policy;
- FIFO/dedup auto-run queue;
- cancellation/progress/resume state;
- explicit Yandex cloud-consent gate;
- Qt-free `normalization_presentation` для controls/process/result state;
- сохранена ручная review/apply модель результата.

Product PR #94 squash-merged в `main` как `c0c2cdf18330cf5cdf9039cbb60b044b5a27764c`.

### Slice 15 — LaTeX monitor UI orchestration extraction

Реализовано:

- Qt-free `LatexMonitorCoordinator`;
- enabled/disabled lifecycle;
- manual/periodic/enable scan triggers;
- single-flight guard;
- Qt-free `latex_monitor_presentation`;
- `RemoteLatexService`, `QTimer` и worker transport остались в соответствующих infrastructure/UI слоях;
- `content_service.activity("latex-monitor")`, remote branch protocol, `pipeline.save_state(..., force_status=True)` и fix-request semantics сохранены;
- manual `compile_local_tex()` не смешивался с monitor workflow.

Product PR #97 squash-merged в `main` как `c7bb109db8cc93373f9ece2a9f9615584c5fd64b`.

## 4. Завершённый шаг — Wave 3 / Slice 16

### Application shutdown coordinator

**Цель:** вынести decision/state безопасного завершения приложения из Qt `closeEvent()` за тестируемую application boundary, сохранив recording finalize safety, cancellation normalization, корректный drain фоновых задач и persisted transcription queue.

Реализовано:

- новый Qt-free `application/shutdown.py` с `ShutdownCoordinator`;
- явные фазы `IDLE / DRAINING / READY`;
- typed close actions `ACCEPT / PROMPT / TRY_IMMEDIATE / IGNORE`;
- `ShutdownRuntimeSnapshot` различает:
  - active recording;
  - recording finalize in-flight;
  - generic workers;
  - `transcription_busy` как причину prompt;
  - `transcription_running` как drain barrier;
  - наличие cancellable normalization;
- `ShutdownDrainPlan` выдаёт однократные side-effect actions для cancellation, transcription shutdown, runtime quiesce и safe recording finalize;
- повторный close во время draining возвращает `IGNORE` и не повторяет destructive side effects;
- ready transition выполняется только после снятия recording/worker/transcription barriers;
- persisted transcription queue не очищается при shutdown.

### Production adapter Slice 16

Чтобы не выполнять рискованную механическую миграцию большого `ui/app.py`, введён отдельный `ui/shutdown_app.py`.

Он вставлен в cooperative C3 MRO между teacher-cockpit/publication слоем и `concurrent_app`:

```text
recording_recovery
→ recording_finalize
→ audio_resilient
→ transcript_publication
→ shutdown_app
→ concurrent_app
→ ui.app
```

Это сохраняет важный порядок:

1. `transcript_publication_app.closeEvent()` сначала сохраняет UI/cockpit session;
2. затем `shutdown_app.closeEvent()` принимает typed shutdown decision;
3. legacy `concurrent_app/base_app.closeEvent()` больше не является production policy path.

`BackgroundTaskCoordinator.begin_shutdown()` интегрирован в новый adapter, поэтому deferred/retry tasks перестают запускаться, а running background tasks получают cancellation. Active normalization, transcription thread и recording finalize сохраняют прежние safety semantics.

Исторические `_shutdown_requested/_shutdown_ready` пока остаются в нижних слоях как **compatibility mirrors** для `audio_resilient_app` и maintenance gates. Новый shutdown adapter их записывает, но не читает как источник lifecycle policy. Источником истины является `ShutdownCoordinator`.

### Tests / CI Slice 16

Добавлены:

- `tests/test_shutdown_coordinator.py` — immediate close, busy→prompt, cancel, confirmed drain, independent barriers, idempotency и ready transition;
- `tests/test_shutdown_ui_architecture.py` — Qt-free application boundary, typed production adapter, compatibility-mirror contract и запрет очистки persisted queue;
- обновлён `tests/test_production_composition.py` для нового responsibility-bearing MRO.

Первый PR CI обнаружил только `Ruff I001` в новом architecture test. Import formatting исправлен без изменения production semantics.

Exact feature head `4284eac1ff7d35a1d944a729b211f1a03155cd0f` прошёл:

- Privacy History Gate — SUCCESS;
- accessibility/scaling 100 / 125 / 150 / 200% — SUCCESS;
- lint / compile / privacy import / secret scan / contracts / whitespace на Windows Python 3.11–3.14;
- перед merge подтверждены независимые full-suite SUCCESS на Python 3.12 и 3.14.

Product PR #99 squash-merged в `main` как:

```text
029705c256045fe6eeedd771aa01e23b8087041d
```

Итоговый product diff: 7 файлов, +558 / −2.

## 5. Следующий шаг — Wave 3 / Slice 17

### Teacher cockpit / parallel-review synchronization

**Цель:** сделать recording context, review context и Teacher Cockpit согласованными через typed state/snapshot boundary вместо прямой интроспекции Qt window и разрозненных widget-driven refresh calls.

### Фактическое состояние перед Slice 17

Сейчас:

- `TeacherCockpitController.refresh()` каждые 30 секунд вызывает `build_cockpit_snapshot(window)`;
- `teacher_cockpit_data.py` Qt-free по импортам, но читает `window`, `workers`, `crm_store`, `pipeline.store`, `lesson`, `students` через динамическую интроспекцию;
- `concurrent_app` хранит отдельные понятия `recording_lesson` и `review_lesson`, но синхронизация presentation выполняется через `_sync_parallel_review_ui()`;
- `ParallelReviewPolicy` уже защищает важные инварианты: review можно открыть во время записи, playback запрещён во время recording, recording form нельзя подменять review lesson;
- context bar, processing queue, lesson transitions, recording callbacks и cockpit refresh вызывают обновления независимо друг от друга;
- 30-секундный cockpit timer полезен как fallback, но не должен быть основным механизмом согласования состояния после локальных событий.

### План Slice 17

1. Проинвентаризировать все producer-ы workspace state:
   - recording start/stop/finalize/recovery;
   - выбор/open review lesson;
   - transcription/normalization/publication transitions;
   - trash/purge/archive changes;
   - CRM/schedule updates;
   - background task lifecycle.
2. Ввести Qt-free typed context model для как минимум двух независимых контекстов:
   - active recording context;
   - current review context.
3. Централизовать policy выбора/смены review context во время активной записи, сохранив:
   - `review_open_allowed=True`;
   - запрет playback при recording busy;
   - запрет восстановления review lesson в recording form при recording busy;
   - stop button всегда относится только к active recording.
4. Убрать использование Qt window как неявного data contract для cockpit snapshot:
   - snapshot builder должен получать typed inputs/ports;
   - CRM/store access остаётся query/infrastructure concern;
   - UI widgets не должны быть source of truth для pipeline state.
5. Добавить event-driven invalidation/refresh для локальных изменений workspace state.
6. Сохранить 30-секундный timer как defensive fallback для внешних/периодических изменений, но не полагаться на него после внутренних transitions.
7. Централизовать parallel-context presentation (`recording + review + elapsed`) поверх typed snapshot.
8. Обработать stale review context:
   - удалённый/trashed lesson;
   - завершённый publish/apply transition;
   - смена selected lesson;
   - recovery/open-from-processing.
9. Добавить unit tests для context transitions и regression tests для сценария:
   - идёт запись ученика A;
   - преподаватель открывает/проверяет занятие ученика B;
   - recording controls остаются привязаны к A;
   - review/publish controls относятся к B;
   - playback B заблокирован, пока запись A активна.
10. Добавить architecture gates против возврата `build_cockpit_snapshot(window)` / raw widget inspection как центрального synchronization mechanism.
11. Не смешивать Slice 17 с визуальным редизайном Teacher Cockpit, новой CRM-функциональностью или изменением publication protocol.

### Definition of Done Slice 17

- recording/review workspace contexts имеют typed testable model;
- cockpit snapshot строится из явных data inputs, а не из произвольного Qt window object;
- internal state transitions обновляют cockpit/parallel context event-driven;
- 30-second timer остаётся только fallback refresh;
- активная запись и открытый review lesson не могут взаимно подменить контекст;
- playback safety во время recording сохраняется;
- stale review context корректно инвалидируется;
- production UI остаётся тонким renderer/router слоя;
- Windows CI, privacy gate и scaling matrix зелёные;
- минимум два независимых full-suite success;
- PR squash-merged в `main`.

## 6. Инварианты разработки

При дальнейших изменениях сохранять:

- **audio-first recovery:** ошибки metadata не должны мешать физическому восстановлению WAV;
- **no rollback of progressed Lesson status:** recovery не откатывает уже продвинувшееся занятие назад;
- **recording safety over convenience:** shutdown/cancellation не должны терять safety-critical finalize callbacks;
- **SQLite as source of truth** для локального архива и pipeline metadata;
- **local-first privacy:** аудио не отправляется во внешние сервисы;
- **explicit cloud consent** для Yandex AI Studio;
- **no concrete audio infrastructure in base UI**;
- **no Qt in application use cases/coordinators**;
- **stable device identity** не должна зависеть только от transient PortAudio index;
- **presentation mapping не возвращается в application policy**;
- persisted transcription queue не очищается при обычном shutdown;
- recording context и review context должны оставаться независимыми во время parallel review;
- любое изменение recording/shutdown lifecycle сопровождается regression/architecture tests.

## 7. Рабочий порядок для следующих slices

Для каждого slice:

1. зафиксировать фактическую текущую зависимость по `main`;
2. определить минимальную архитектурную границу;
3. не смешивать соседние refactor-задачи в один PR;
4. сначала добавить/перенести contract и тесты;
5. переключить production path на новую boundary;
6. физически удалить dead legacy orchestration там, где это безопасно и не требует рискованной механической миграции;
7. выполнить self-review diff;
8. прогнать Windows CI и policy gates;
9. получить минимум два независимых full-suite success для существенных orchestration/refactor changes;
10. squash merge с проверкой expected head SHA;
11. обновить `PLAN.md` и соответствующий current-state блок README.
