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

Базовый `ui/app.py` остаётся общим presentation shell и набором command ports. Он больше не должен владеть транзакциями старта/остановки/восстановления записи, preflight capture, concrete recorder construction или hardware discovery.

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

На момент завершения Slice 9 production path проверен Windows CI на Python 3.11–3.14; для merge были получены независимые полные успешные regression runs, privacy gate и scaling matrix 100/125/150/200%.

## 3. Следующий шаг — Wave 2 / Slice 10

### Recording Runtime Health / Warning Policy extraction

**Цель:** убрать из `ui/app.py` политику интерпретации runtime health recorder-а. Qt должен только отображать уже вычисленное состояние и инициировать stop-команду при terminal assessment.

Сейчас `_tick()` одновременно:

- обновляет duration;
- читает `recorder.levels` и `recorder.health`;
- вычисляет total dropped blocks;
- форматирует health label;
- интерпретирует `stream_errors`;
- определяет потерю callback по `device_timeout_seconds`;
- применяет `silence_warning_seconds`;
- собирает и дедуплицирует warning text;
- решает, когда требуется аварийно завершить запись.

Эта policy должна стать Qt-free и детерминированно тестироваться отдельно от GUI.

### Предлагаемая архитектура

Новый модуль:

```text
src/tutor_assistant/application/recording_health.py
```

Предлагаемые типы:

```text
RecordingHealthPolicy
RecordingHealthSample
RecordingHealthAssessment
RecordingHealthSeverity
RecordingHealthAction
```

`RecordingHealthPolicy` содержит пороги, приходящие из конфигурации, а не читает `AppConfig` напрямую:

- `device_timeout_seconds`;
- `silence_warning_seconds`.

`RecordingHealthSample` получает только immutable runtime values:

- microphone/system levels;
- queue percentages;
- dropped blocks;
- writer latency;
- silence durations;
- callback ages;
- stream errors;
- reconnect attempts;
- elapsed recording seconds.

`RecordingHealthAssessment` должен возвращать presentation-neutral результат:

- normalized levels;
- concise health summary data;
- warning messages;
- severity;
- optional terminal stop reason;
- признак восстановления normal state после предупреждения.

### Правила, которые необходимо сохранить без изменения поведения

1. Любой `stream_errors` → terminal stop с причиной ошибки аудиоустройства.
2. Callback age выше `device_timeout_seconds` после стартового grace period → terminal stop с сохранением доступных чанков.
3. Тишина микрофона или system audio дольше `silence_warning_seconds` → warning, но не stop.
4. Любые dropped blocks → warning.
5. Повторяющийся одинаковый warning не должен генерировать повторный UI/log event каждую секунду.
6. После нормализации параметров warning state сбрасывается и UI возвращается к состоянию «Идёт запись».
7. UI остаётся владельцем только visual rendering, logging presentation event и вызова `_stop_recording_async(reason)`.

### Тесты Slice 10

Обязательны unit tests без Qt:

- healthy sample;
- microphone silence;
- system silence;
- simultaneous silence;
- dropped blocks;
- stream error;
- microphone callback timeout;
- system callback timeout;
- timeout не срабатывает до grace period;
- reconnect count остаётся informational metric;
- формат assessment не зависит от PySide/recorder implementation.

Architecture gates:

- `application/recording_health.py` не импортирует PySide6;
- не импортирует `DualRecorder`;
- не читает `AppConfig`;
- base `_tick()` не содержит policy comparisons с `stream_errors`, callback ages, silence limits и dropped-block rules;
- production behaviour по stop/warning сохраняется regression tests.

### Definition of Done

Slice 10 считается завершённым, когда:

- runtime health policy полностью тестируется без Qt;
- `ui/app.py::_tick()` становится presentation adapter вместо policy engine;
- recording stop semantics не изменены;
- Windows CI проходит lint/compile/import/contracts/full suite;
- privacy history gate и accessibility scaling остаются зелёными;
- PR squash-merged в `main`.

## 4. Последующие шаги

### Wave 2 / Slice 11 — Recording presentation extraction

После health policy можно вынести из base UI оставшееся состояние presentation recording panel:

- duration formatting;
- health label rendering;
- level-bar normalization;
- warning tone transitions;
- active/inactive visual state.

Цель — уменьшить `ui/app.py` без переноса business policy в другой Qt-класс.

### Wave 2 / Slice 12 — Production composition cleanup

Проверить MRO и промежуточные adapters после завершения recording decomposition:

- удалить ставшие пустыми compatibility overrides;
- сократить module-level monkeypatch/composition tricks;
- явно документировать production composition root;
- закрепить его architecture tests.

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
