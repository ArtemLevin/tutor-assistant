# Tutor Assistant — Development Plan

**Актуальность:** 18 августа 2026 года  
**Текущая версия:** `0.22.1`  
**Основная ветка:** `main`

## 1. Текущее состояние

Wave 2 архитектурной стабилизации production-контура записи завершён. В Wave 3 завершён первый slice: orchestration локальной очереди транскрибации отделена от базового Qt-окна. Следующий фокус — LLM normalization orchestration.

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

## 5. Следующий шаг — Wave 3 / Slice 14

### LLM normalization presentation/orchestration extraction

**Цель:** отделить scheduling и lifecycle LLM-фильтрации от `ui/app.py`, сохранив ручное подтверждение результата, cancellation/resume, cloud-consent policy и запрет одновременной CPU-heavy Ollama/Whisper обработки.

План Slice 14:

1. проинвентаризировать `normalize_current_transcript`, `_pump_auto_normalization`, worker callbacks, resume-confirmation и control-state synchronization;
2. выделить Qt-free normalization coordinator/state model для start eligibility, queued auto-normalization, completion/failure/cancel/resume decisions;
3. оставить `NormalizationWorker` транспортным Qt adapter, не перенося concrete Ollama/Yandex providers в UI abstraction;
4. ввести typed presentation state для process status/primary action вместо распределённого форматирования по `ui/app.py`;
5. сохранить explicit Yandex cloud consent и невозможность auto-cloud processing без ручного согласия;
6. сохранить mutual exclusion с active Whisper queue через transcription coordinator contract;
7. добавить pure unit tests для manual start, auto queue, busy barriers, cancel, resume/indeterminate, success/failure и cloud-consent gates;
8. добавить architecture gates, не смешивая Slice 14 с LaTeX monitor или shutdown refactor.

### Definition of Done Slice 14

- normalization lifecycle decisions тестируются без PySide6;
- Qt widgets не являются source of truth для normalization execution state;
- Ollama/Whisper mutual exclusion сохранён;
- Yandex cloud processing по-прежнему требует explicit consent;
- cancellation/resume и manual review semantics не изменены;
- recording/transcription boundaries остаются неизменными;
- Windows CI, privacy gate и scaling matrix зелёные;
- минимум два независимых full-suite success;
- PR squash-merged в `main`.

### Последующие Wave 3 slices

После Slice 14:

1. LaTeX monitor UI orchestration;
2. application shutdown coordinator;
3. teacher cockpit / parallel review synchronization.

Каждый этап следует тому же правилу: сначала выделяется реальная policy/orchestration в Qt-free boundary, затем production path переключается на неё, после чего dead legacy-код физически удаляется.

## 6. Инварианты разработки

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

## 7. Рабочий порядок для следующих slices

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
