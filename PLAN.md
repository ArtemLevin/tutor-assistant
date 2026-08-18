# Tutor Assistant — Development Plan

**Актуальность:** 18 августа 2026 года  
**Текущая версия:** `0.22.1`  
**Основная ветка:** `main`

## 1. Текущее состояние

Проект находится в фазе архитектурной стабилизации production-контура записи. Критический путь записи уже вынесен из legacy orchestration базового Qt-окна в application layer и production adapters.

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

На момент завершения Slice 11 production path прошёл lint/compile/import/contracts на Windows matrix Python 3.11–3.14; перед merge получены независимые полные успешные regression runs на Python 3.12, 3.13 и 3.14, а privacy gate и scaling matrix 100/125/150/200% были зелёными.

## 3. Завершённый шаг — Wave 2 / Slice 11

### Recording presentation extraction

**Цель:** отделить presentation formatting recording panel от policy и orchestration, сохранив base Qt window тонким renderer-ом уже вычисленного view-state.

Реализован новый Qt-free модуль:

```text
src/tutor_assistant/ui/recording_presentation.py
```

Он централизует:

- форматирование duration в `HH:MM:SS`;
- нормализацию live audio levels в диапазон progress bar `0..100`;
- формат health summary для queue pressure, dropped blocks, writer latency, silence и reconnect count;
- presentation mapping нового warning и перехода обратно в healthy state;
- канонические visual phases `READY`, `RECORDING`, `SAVING`, `SAVED`, `RECOVERY_REQUIRED`, `FAILED`.

`RecordingHealthAssessment` снова presentation-neutral: из application model удалены `microphone_level_percent`, `system_level_percent` и `warning_text`.

Base `_tick()` теперь выполняет только:

1. инкремент elapsed seconds;
2. получение runtime health assessment при активном recorder;
3. построение `RecordingTickPresentation`;
4. применение готового view-state к Qt widgets;
5. safety-critical `_stop_recording_async(...)` при terminal action.

Terminal stop имеет приоритет над warning presentation: если terminal assessment одновременно содержит warning-факты, промежуточный warning-status/log event не показывается перед safe stop.

Production start/finalize adapters используют единый `_set_recording_panel_phase(...)` вместо ручных `setText`, `setProperty("active", ...)` и `refresh_style`.

### Тестовый контур Slice 11

Добавлены unit tests без Qt для:

- duration formatting;
- level clamping/normalization;
- live health summary;
- inactive tick;
- warning event;
- warning de-duplication;
- warning recovery;
- terminal-stop precedence;
- всех canonical recording panel phases.

Architecture gates фиксируют:

- `recording_presentation.py` не зависит от PySide6, concrete recorder infrastructure или `AppConfig`;
- application health assessment не содержит view-formatting helpers;
- base `_tick()` не форматирует widgets/text самостоятельно;
- start/finalize adapters не стилизуют recording-state label напрямую;
- base phase renderer является единственной точкой применения text/active/style к recording-state label.

Во время первого CI Ruff обнаружил один неиспользуемый import в новом architecture test (`F401`); import удалён, после чего свежий exact head прошёл требуемые gates и был squash-merged.

## 4. Следующий шаг и последующие slices

### Следующий шаг — Wave 2 / Slice 12 — Production composition cleanup

После завершения recording policy/presentation extraction необходимо упростить production composition и MRO.

План Slice 12:

1. проинвентаризировать цепочку `recording_recovery_app → recording_finalize_app → audio_resilient_app → transcript_publication_app → concurrent_app → ui.app`;
2. выявить пустые или compatibility-only overrides и классы, которые больше не несут самостоятельной ответственности;
3. удалить ставшие мёртвыми module-level monkeypatch/composition tricks;
4. определить явный production composition root без циклического присваивания `base_app.MainWindow = MainWindow`, если это возможно без изменения entrypoint semantics;
5. сохранить отдельное владение Start / Stop-Finalize / Recovery там, где оно по-прежнему обеспечивает понятную границу;
6. закрепить финальную MRO/composition boundary architecture tests;
7. не смешивать этот cleanup с Wave 3 transcription/normalization refactors.

### Definition of Done Slice 12

- production entrypoint остаётся стабильным;
- MRO содержит только слои с реальной ответственностью;
- нет ставших ненужными compatibility bridges/monkeypatches;
- composition root документирован и проверяется тестами;
- start/stop/recovery safety semantics не меняются;
- Windows CI, privacy gate и scaling matrix зелёные;
- PR squash-merged в `main`.

### Wave 3 — декомпозиция общего `ui/app.py`

После завершения recording-wave перейти к следующим крупным зонам god-object:

1. transcription queue presentation/orchestration;
2. LLM normalization presentation orchestration;
3. LaTeX monitor UI orchestration;
4. application shutdown coordinator;
5. teacher cockpit / parallel review synchronization.

Каждый этап должен следовать тому же правилу: сначала extraction реальной policy/orchestration в Qt-free layer, затем физическое удаление dead legacy-кода.

## 5. Инварианты разработки

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

## 6. Рабочий порядок для следующих slices

Для каждого slice:

1. зафиксировать фактическую текущую зависимость по `main`;
2. определить минимальную архитектурную границу;
3. не смешивать соседние refactor-задачи в один PR;
4. сначала добавить/перенести contract и тесты;
5. физически удалить старую orchestration после переключения production path;
6. выполнить self-review diff;
7. прогнать Windows CI и policy gates;
8. получить минимум два независимых full-suite success для существенных recording changes;
9. squash merge с проверкой expected head SHA;
10. обновлять этот `PLAN.md`, когда завершённый slice меняет следующую архитектурную границу.
