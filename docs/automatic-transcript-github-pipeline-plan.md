# План реализации автоматической транскрибации и публикации транскрипта

## 1. Цель

Добавить в Tutor Assistant отдельный opt-in режим занятия, который преподаватель включает **до начала записи**.

Если режим включён, после нажатия «Завершить запись» сервис должен без дополнительных действий пользователя:

1. корректно завершить запись;
2. сформировать итоговое аудио занятия;
3. автоматически поставить аудио в persistent очередь транскрибации;
4. выполнить локальную транскрибацию;
5. сохранить полученный текст как durable immutable revision в SQLite;
6. автоматически поставить транскрипт в persistent очередь публикации;
7. отправить **ровно один текстовый файл** в приватный GitHub-репозиторий;
8. подтвердить, что опубликован именно ожидаемый commit/content;
9. корректно восстановить незавершённый pipeline после перезапуска приложения.

Целевой GitHub-путь:

```text
<student.repository_folder>/transcript/DD.MM.YY.txt
```

Пример для занятия 4 октября 2026 года:

```text
students/ivan_petrov/transcript/04.10.26.txt
```

Имя файла строится из `lesson.lesson_date`, а не из текущей даты системы.

---

## 2. Пользовательский контракт

### Manual mode

Если новая галочка выключена, существующий workflow должен остаться совместимым:

```text
recording
→ stop/finalize audio
→ optional auto-transcription according to current quick profile
→ REVIEW_REQUIRED
→ teacher review
→ approve
→ READY
→ manual publish
```

Существующая семантика `READY = teacher-approved transcript` сохраняется.

### Automatic mode

Перед записью преподаватель явно включает:

```text
☑ Автоматически транскрибировать и отправить на GitHub
```

После этого workflow:

```text
recording
→ stop/finalize audio
→ persistent transcription queue
→ ASR
→ immutable automatic transcript revision
→ persistent publication queue
→ verified GitHub publication
→ PUBLISHED
```

После успешного `stop` для happy path пользователь больше ничего не подтверждает и не нажимает.

---

## 3. Основные invariants

Реализация должна сохранять следующие инварианты.

1. Новый режим является **opt-in для конкретного Lesson**, а не глобальным переключателем приложения.
2. Значение режима фиксируется до старта recorder и сохраняется в durable lesson state.
3. Старые Lesson без нового поля автоматически считаются manual.
4. Automatic mode всегда включает auto-transcription независимо от `LaunchProfile.auto_transcribe`.
5. Ошибка GitHub не переводит успешно записанное и транскрибированное занятие в состояние потери данных.
6. Ошибка транскрибации не уничтожает готовое аудио.
7. Публикация использует durable SQLite revision как source of truth, а не изменяемый файл.
8. Manual publisher по-прежнему требует teacher approval.
9. Automatic publisher не должен выдавать машинный transcript за teacher-approved.
10. В GitHub уходит только ожидаемый `.txt`; аудио, JSON, manifests, TEX/PDF и служебные файлы остаются локально.
11. Повтор после crash/network failure идемпотентен.
12. Существующий `DD.MM.YY.txt` с другим содержимым никогда не перезаписывается молча.
13. Все Git publication operations сериализуются через единую coordination boundary.
14. Следующее занятие можно начать, пока предыдущий transcript транскрибируется или публикуется.

---

## 4. Этап 1 — persisted processing mode

### Файл

```text
src/tutor_assistant/domain.py
```

Добавить доменный enum:

```python
class LessonProcessingMode(StrEnum):
    MANUAL = "manual"
    AUTO_TRANSCRIPT_GITHUB = "auto_transcript_github"
```

Расширить `PipelineOptions`:

```python
class PipelineOptions(BaseModel):
    latex: bool = True
    compile_pdf: bool = True
    poster: bool = True
    web: bool = True
    update_student_index: bool = True
    processing_mode: LessonProcessingMode = LessonProcessingMode.MANUAL
```

### Почему enum, а не два boolean

Не вводить независимые:

```text
auto_transcribe
auto_publish
```

иначе появляются некорректные комбинации вроде `auto_transcribe=false + auto_publish=true`.

### Backward compatibility

Старые `lesson.json` и JSON payload в таблице `lessons` не содержат поле `processing_mode`. Default `MANUAL` должен обеспечить прозрачную загрузку старых данных без отдельной SQL migration.

### Проверки

- old serialized Lesson → `MANUAL`;
- round-trip `AUTO_TRANSCRIPT_GITHUB`;
- persisted mode переживает restart.

---

## 5. Этап 2 — checkbox в quick-start UI

### Файл

```text
src/tutor_assistant/ui/app.py
```

В `_quick_start_page()` добавить checkbox:

```text
☐ Автоматически транскрибировать и отправить на GitHub
```

Подпись/tooltip должны явно сообщать:

```text
После завершения записи сервис автоматически транскрибирует занятие
и отправит транскрипт в приватный GitHub-репозиторий.
```

### UX rules

- default: unchecked;
- не сохранять checkbox как глобальный default между уроками;
- после фактического начала recording checkbox блокируется;
- изменение UI после старта не меняет mode уже созданного Lesson;
- после подготовки следующего занятия checkbox снова unchecked;
- scheduled autostart по умолчанию запускается в `MANUAL`, пока отдельно не появится настройка режима в расписании.

При создании Lesson:

```python
lesson.pipeline.processing_mode = (
    LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB
    if self.quick_automatic_pipeline.isChecked()
    else LessonProcessingMode.MANUAL
)
```

Mode должен быть записан в SQLite/lesson.json **до запуска recorder**.

---

## 6. Этап 3 — разделить старый auto-transcribe и новый auto-pipeline

Существующий `LaunchProfile.auto_transcribe` сохраняется.

Итоговая матрица:

```text
MANUAL + profile.auto_transcribe=false
    recording → manual workflow

MANUAL + profile.auto_transcribe=true
    recording → auto transcription → manual review/publish

AUTO_TRANSCRIPT_GITHUB
    recording → mandatory auto transcription → automatic publication
```

Decision:

```python
should_transcribe = (
    profile.auto_transcribe
    or lesson.pipeline.processing_mode
       == LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB
)
```

Runtime-флаг `_quick_auto_transcribe_active` может остаться для старого UX, но не должен быть source of truth для automatic publication.

---

## 7. Этап 4 — recording finalization → transcription queue

### Файл

```text
src/tutor_assistant/ui/recording_finalize_app.py
```

Automatic mode ставит аудио в transcription queue только после:

```text
RecordingStopOutcome == RECORDED
```

и получения валидного `RecordingResult.mixed_file`.

Decision:

```python
auto_pipeline = (
    recorded_lesson.pipeline.processing_mode
    == LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB
)

if self._quick_auto_transcribe_active or auto_pipeline:
    self._enqueue_transcription(recorded_lesson, result.mixed_file)
```

Не запускать downstream pipeline при:

```text
RECOVERY_REQUIRED
FINALIZATION_FAILED
```

При этих состояниях сохраняются существующие recovery semantics.

---

## 8. Этап 5 — переиспользовать persistent transcription queue

Существующий механизм:

```text
src/tutor_assistant/transcription_queue.py
src/tutor_assistant/application/transcription_queue.py
src/tutor_assistant/store.py
src/tutor_assistant/ui/transcription_worker.py
```

оставить основой автоматической транскрибации.

Queue states:

```text
WAITING
RUNNING
READY
FAILED
```

При restart `RUNNING` восстанавливается как повторно запускаемая работа, а orphan recording может быть реконструирован по durable Lesson state.

Новый feature не должен создавать вторую ASR queue.

---

## 9. Этап 6 — durable automatic transcript revision

После успешного ASR automatic mode должен сохранить immutable transcript revision в SQLite.

Источник текста — canonical cleaned transcript текущего transcription result.

Использовать существующий `StudentContentService.save_transcript(...)`:

```python
revision = content_service.save_transcript(
    lesson.lesson_id,
    cleaned_text,
    path=lesson.artifacts.verified_transcript,
    created_by="automatic-transcription",
)
```

Требования:

- `created_by="automatic-transcription"`;
- сохранить canonical UTF-8 content;
- сохранить SHA-256;
- создать revision до создания publication intent;
- не использовать `created_by="teacher-review"`;
- не делать fake transition в `READY`.

Durability order:

```text
ASR artifacts
→ automatic SQLite revision
→ publication intent
→ GitHub
```

---

## 10. Этап 7 — сохранить семантику READY

Существующее правило:

```text
READY = transcript explicitly approved by teacher
```

не меняется.

Не делать:

```text
automatic ASR → force READY → existing manual publish
```

Вместо этого создать отдельный application authorization path.

### Manual path

```text
teacher review
→ approve_transcript()
→ READY
→ manual publication
```

### Automatic path

```text
processing_mode == AUTO_TRANSCRIPT_GITHUB
+ automatic revision
→ automatic publication use case
```

После подтверждённой remote publication automatic use case может перевести Lesson из `REVIEW_REQUIRED` в `PUBLISHED`. Этот переход должен быть разрешён только через проверенный application-level invariant и иметь отдельные regression tests; не использовать необоснованный `force=True` как основной механизм.

---

## 11. Этап 8 — разделить authorization и Git transport

### Файл

```text
src/tutor_assistant/publisher.py
```

Существующий publisher сейчас совмещает:

- проверку разрешения на публикацию;
- выбор path;
- подготовку payload;
- Git transport;
- remote verification.

Нужно выделить общий transport, который принимает immutable publication payload, например:

```python
@dataclass(frozen=True)
class TranscriptPublicationPayload:
    lesson_id: str
    repository_path: str
    content: str
    content_sha256: str
    revision_number: int
```

Общий transport продолжает выполнять:

1. проверку configured private repository;
2. remote identity verification;
3. fetch `origin/main`;
4. isolated detached worktree;
5. atomic write;
6. staged/outgoing egress guard;
7. Git blob SHA verification;
8. commit;
9. fast-forward push;
10. remote SHA verification;
11. publication journal reconciliation.

### Manual wrapper

Existing `LessonPublisher.publish(...)` остаётся backward compatible:

- требует `READY`;
- использует teacher-approved SQLite revision;
- сохраняет текущий manual path.

### Automatic wrapper/use case

Добавить отдельный application use case, например:

```text
PublishAutomaticTranscriptUseCase
```

Он обязан проверить:

- `processing_mode == AUTO_TRANSCRIPT_GITHUB`;
- revision существует;
- revision относится к данному lesson;
- `created_by == "automatic-transcription"`;
- content SHA корректен;
- target path соответствует automatic policy.

Только после этих проверок вызывается общий Git transport.

---

## 12. Этап 9 — automatic publication path

Добавить отдельный resolver:

```python
def automatic_publication_repository_path(
    lesson: Lesson,
) -> PurePosixPath:
    return (
        PurePosixPath(lesson.student.folder)
        / "transcript"
        / f"{lesson.lesson_date:%d.%m.%y}.txt"
    )
```

Пример:

```text
students/petrov/transcript/04.10.26.txt
```

Manual resolver:

```text
<student>/lessons/<lesson_slug>/transcript.txt
```

не менять в рамках этой feature, чтобы сохранить backward compatibility.

---

## 13. Этап 10 — collision protection

Automatic publisher должен fail closed.

Перед commit проверить remote target.

### Case A — файла нет

```text
publish normally
```

### Case B — файл есть, SHA совпадает

```text
idempotent success
```

Повторный запуск не создаёт новый конфликт и не меняет content.

### Case C — файл есть, SHA отличается

```text
CONFLICT
```

Не выполнять overwrite.

Это защищает от двух занятий одного ученика в одну дату:

```text
transcript/04.10.26.txt
```

не может быть молча заменён вторым Lesson.

---

## 14. Этап 11 — persistent automatic publication queue

Текущий `publication.sqlite3` журналирует уже начатую Git operation, но не является полноценной durable intent queue.

Добавить migration 11:

```text
src/tutor_assistant/content/migrations.py
```

Пример таблицы:

```sql
CREATE TABLE automatic_publication_jobs (
    lesson_id TEXT PRIMARY KEY,
    revision_number INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    repository_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'waiting',
        'running',
        'retry_required',
        'published',
        'conflict',
        'blocked'
    )),
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    next_attempt_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(lesson_id) REFERENCES lessons(lesson_id) ON DELETE CASCADE
);
```

Разделение ответственности:

```text
automatic_publication_jobs
    = durable intent: этот transcript должен быть опубликован

publication.sqlite3
    = durable journal конкретной Git operation
```

Migration должна быть:

- idempotent;
- transactional;
- безопасной для существующих databases;
- покрыта migration test.

---

## 15. Этап 12 — PublicationQueueCoordinator

По аналогии с transcription queue добавить:

```text
src/tutor_assistant/publication_queue.py
src/tutor_assistant/application/publication_queue.py
```

Queue должна быть single-worker.

Основные состояния:

```text
WAITING
→ RUNNING
→ PUBLISHED

RUNNING
→ RETRY_REQUIRED

RUNNING
→ CONFLICT

RUNNING
→ BLOCKED
```

Требования:

- deduplication по `lesson_id`;
- persisted attempts;
- retry не меняет revision/SHA;
- `RUNNING` после crash восстанавливается через reconciliation;
- `CONFLICT` и `BLOCKED` не ретраятся автоматически;
- завершённая job остаётся доступна для diagnostics/history либо очищается только по явной retention policy.

---

## 16. Этап 13 — publication worker

Добавить Qt transport adapter, аналогичный transcription worker:

```text
src/tutor_assistant/ui/publication_worker.py
```

Worker выполняет только transport/thread boundary.

Application decisions должны оставаться Qt-free.

Worker не должен блокировать recorder или следующий урок.

Допустимое параллельное состояние:

```text
Lesson A:
✓ recording
✓ transcription
● GitHub publication

Lesson B:
● recording
```

---

## 17. Этап 14 — atomic handoff ASR → publication

Нельзя создавать publication intent только в UI callback `_background_transcription_ready()`.

Опасное окно:

```text
pipeline.transcribe() persisted transcript
→ process crash
→ Qt success signal not handled
→ publication never enqueued
```

Поэтому application/persistence boundary должна гарантировать последовательность:

```text
persist ASR artifacts
→ persist automatic transcript revision
→ persist publication job
→ return transcription success
```

UI callback после этого только:

- отмечает transcription queue как completed;
- запускает publication pump;
- обновляет presentation state.

Если полностью атомарная SQL transaction между transcript revision и publication job архитектурно доступна без нарушения текущих boundaries — использовать её. Если нет, startup reconciliation обязан закрывать это окно детерминированно.

---

## 18. Этап 15 — startup reconciliation

При старте приложения восстановить незавершённые automatic pipelines.

Для Lesson с `AUTO_TRANSCRIPT_GITHUB`:

### Audio есть, transcription отсутствует

Если Lesson находится в `RECORDED`/`TRANSCRIBING`, аудио существует, а runnable transcription job отсутствует:

```text
restore/enqueue transcription
```

### Automatic revision есть, publication job отсутствует

```text
create publication job
```

### Publication job RUNNING

Сверить `publication.sqlite3` и remote:

- remote == local commit → finish idempotently;
- remote == expected base → безопасный retry;
- remote отличается от expected/local → conflict;
- remote временно недоступен → retry_required/indeterminate без потери intent.

### Publication уже подтверждена

Не делать второй push. Восстановить local job/Lesson state из durable journal.

---

## 19. Этап 16 — retry policy

Ошибки разделить по типу.

### Transient

Примеры:

- network timeout;
- temporary GitHub unavailability;
- temporary DNS/transport failure;
- remote verification temporarily unavailable.

Переход:

```text
RETRY_REQUIRED
```

Использовать bounded backoff, например:

```text
30 sec → 2 min → 10 min → 30 min
```

После ограниченного числа автоматических попыток оставить job доступной для ручного retry.

### Configuration/security block

Примеры:

- target repository public;
- remote identity mismatch;
- missing authentication;
- unsafe repository path;
- push disabled.

Переход:

```text
BLOCKED
```

Автоматический retry до изменения конфигурации не выполнять.

### Conflict

Примеры:

- `DD.MM.YY.txt` уже существует с другим SHA;
- remote head изменился в неопределённую сторону;
- reconciliation обнаружил third SHA.

Переход:

```text
CONFLICT
```

Автоматический retry запрещён.

---

## 20. Этап 17 — Lesson state после automatic publication

После ASR Lesson может находиться в `REVIEW_REQUIRED`, при этом automatic publication выполняется отдельно.

После подтверждённой remote publication:

```text
REVIEW_REQUIRED + AUTO_TRANSCRIPT_GITHUB + remote_verified
→ PUBLISHED
```

Требуется явно обновить domain transition contract и regression tests.

Manual path остаётся:

```text
REVIEW_REQUIRED
→ READY
→ PUBLISHED
```

Нельзя ослабить `LessonPublisher.publish()` так, чтобы manual UI смог публиковать обычный `REVIEW_REQUIRED` Lesson.

---

## 21. Этап 18 — единая publication coordination boundary

Manual и automatic publication должны использовать одну coordination boundary:

```text
manual publish ─────┐
                    ├→ RepositoryPublicationCoordinator → Git
automatic queue ────┘
```

Причины:

- обе операции меняют одну `origin/main`;
- параллельные fetch/commit/push повышают вероятность конфликтов;
- единый coordinator упрощает shutdown/recovery.

Внутри процесса — не более одной активной Git publication operation.

Cross-process safety дополнительно обеспечивается существующими:

- expected remote SHA;
- fast-forward push;
- publication journal;
- remote verification;
- conflict detection.

---

## 22. Этап 19 — UI status/presentation

Для automatic mode не показывать modal dialog после каждого успешного шага.

Предпочтительный progress:

```text
● Сохраняю запись…
```

```text
✓ Запись сохранена
● Транскрибирую…
```

```text
✓ Запись сохранена
✓ Транскрипт готов
● Отправляю на GitHub…
```

Успех:

```text
✓ Занятие обработано
GitHub: transcript/04.10.26.txt
```

Transient failure:

```text
✓ Запись сохранена
✓ Транскрипт готов
! GitHub временно недоступен · публикация ожидает повтора
```

Conflict:

```text
✓ Запись сохранена
✓ Транскрипт готов
! transcript/04.10.26.txt уже существует с другим содержимым
```

Для failed/retry states дать действия:

```text
[Повторить публикацию] [Открыть занятие]
```

GitHub processing не должен блокировать старт следующего Lesson.

---

## 23. Этап 20 — shutdown semantics

При закрытии приложения:

- не начинать новую publication job после начала shutdown;
- текущую Git operation либо корректно завершить, либо оставить durable state для reconciliation;
- не помечать job как `PUBLISHED`, пока remote SHA не проверен;
- не удалять automatic revision;
- не удалять publication intent.

Новый worker должен участвовать в общем shutdown coordination рядом с transcription/background workers.

---

## 24. Этап 21 — тестовый контракт

### Domain tests

Проверить:

- legacy Lesson без mode → `MANUAL`;
- serialization round-trip automatic mode;
- допустимый automatic transition после verified publication;
- manual semantics `READY` не изменены.

### Quick-start/UI tests

Файлы-кандидаты:

```text
tests/test_quick_start.py
tests/test_ux2_core_workflows_gui.py
```

Проверить:

- checkbox default off;
- checked → Lesson получает `AUTO_TRANSCRIPT_GITHUB`;
- mode persist до recorder start;
- checkbox нельзя использовать для изменения активного Lesson;
- следующий Lesson снова unchecked;
- scheduled start default manual.

### Recording integration tests

Проверить:

- AUTO + successful stop → transcription enqueue;
- AUTO + `RECOVERY_REQUIRED` → no downstream enqueue;
- AUTO + finalization failed → no downstream enqueue;
- MANUAL + legacy `auto_transcribe=true` сохраняет старое поведение.

### Transcript revision tests

Проверить:

- successful AUTO ASR создаёт revision;
- `created_by == "automatic-transcription"`;
- SHA соответствует canonical content;
- revision создаётся до publication job;
- retry ASR/reconciliation не создаёт неконтролируемые duplicate revisions.

### Publication path tests

```text
lesson_date = 2026-10-04
→ students/test/transcript/04.10.26.txt
```

Отдельно закрепить, что resolver не использует `datetime.now()`.

### Collision tests

- target absent → publish;
- target with same SHA → idempotent success;
- target with different SHA → conflict;
- existing content не изменяется.

### Publication queue tests

Проверить:

- jobs run sequentially;
- active job deduplicated;
- status persisted;
- attempts increment correctly;
- `RUNNING` restart recovery;
- retry after transient failure;
- conflict terminal;
- blocked terminal until explicit retry/config change;
- completed job not republished.

### Crash/recovery tests

Критические boundaries:

1. crash after audio finalization before transcription enqueue;
2. crash after transcription starts;
3. crash after ASR artifacts persisted before automatic revision;
4. crash after revision persisted before publication intent;
5. crash after publication intent before Git operation;
6. crash after Git commit before push;
7. crash after push before remote verification;
8. crash after remote verification before local completion.

После restart система должна сходиться к единственному корректному результату без потери данных и без duplicate publication.

### Security tests

Проверить:

- manual `REVIEW_REQUIRED` не может использовать automatic authorization;
- auto publisher отклоняет Lesson без automatic mode;
- auto publisher отклоняет teacher/manual revision, если ожидается automatic revision;
- public repository блокируется;
- remote identity mismatch блокируется;
- path traversal невозможен;
- outgoing diff содержит ровно один ожидаемый text path;
- audio/JSON/TEX/PDF не попадают в Git egress.

### Concurrency tests

Проверить:

- две automatic jobs сериализуются;
- manual publish и automatic publish не выполняют Git mutation параллельно;
- два concurrent begin для одного Lesson дают ровно одну active operation;
- remote head change приводит к conflict, а не force-push.

### End-to-end test

Минимальный автоматический сценарий:

```text
checkbox ON
→ start
→ successful stop result
→ transcription queue
→ ASR
→ automatic SQLite revision
→ publication queue
→ local/fake Git remote
→ transcript/04.10.26.txt
→ exact expected bytes
→ verified remote commit
→ Lesson PUBLISHED
```

Mirror test:

```text
checkbox OFF
→ automatic Git publication отсутствует
→ old manual workflow preserved
```

---

## 25. Этап 22 — документация

После реализации обновить:

```text
docs/transcript-only-publication.md
docs/pr46-publication-integrity.md
README.md
PLAN.md
```

Документация должна различать:

```text
Manual publication:
requires explicit teacher approval.

Automatic publication:
allowed only when AUTO_TRANSCRIPT_GITHUB
was explicitly selected before recording.
```

Это privacy/security boundary и она должна быть сформулирована явно.

---

## 26. Рекомендуемая последовательность implementation slices

### Slice 1 — Domain + UI flag

- `LessonProcessingMode`;
- persisted `processing_mode`;
- checkbox;
- old Lesson compatibility;
- unit/UI tests.

### Slice 2 — Recording → mandatory ASR in auto mode

- stop decision;
- guaranteed transcription enqueue;
- failure guards;
- regression tests.

### Slice 3 — Automatic transcript revision

- immutable revision;
- `created_by="automatic-transcription"`;
- SHA checks;
- persistence/recovery tests.

### Slice 4 — Publisher transport refactor

- shared immutable payload;
- preserve manual wrapper;
- automatic authorization use case;
- no behavioral regression in manual publisher.

### Slice 5 — Automatic path + collision contract

- `transcript/DD.MM.YY.txt`;
- idempotent same-SHA behavior;
- different-SHA conflict;
- tests.

### Slice 6 — Migration 11 + publication queue

- schema;
- storage;
- queue;
- coordinator;
- retry/error taxonomy;
- tests.

### Slice 7 — Publication worker + orchestration

- background worker;
- pump;
- transcription-to-publication handoff;
- single publication execution boundary.

### Slice 8 — Startup recovery

- restore/reconcile publication jobs;
- reconcile publication journal;
- crash-boundary tests.

### Slice 9 — UX/status/retry

- progress states;
- retry action;
- conflict presentation;
- non-blocking next lesson.

### Slice 10 — Full verification

- focused tests;
- full test suite;
- ruff;
- compileall;
- type/static checks if configured;
- release gate;
- self-review of diff;
- documentation update.

---

## 27. Definition of Done

Feature считается завершённой только если выполнен следующий реальный сценарий:

```text
1. Запустить Tutor Assistant.
2. Выбрать ученика и тему.
3. Поставить:
   ☑ Автоматически транскрибировать и отправить на GitHub
4. Начать запись.
5. Завершить запись.
6. После stop больше ничего не нажимать.
7. Убедиться, что итоговое аудио сформировано.
8. Убедиться, что ASR завершился.
9. Убедиться, что в SQLite существует automatic transcript revision.
10. Убедиться, что в private GitHub/main появился:
    <student>/transcript/DD.MM.YY.txt
11. Проверить exact content SHA.
12. Проверить remote commit verification.
13. Убедиться, что Lesson достиг PUBLISHED.
14. Повторить с network failure и подтвердить recovery/retry.
15. Повторить с restart на промежуточном этапе и подтвердить convergence.
16. Повторить с checkbox OFF и подтвердить неизменность старого manual workflow.
```

Дополнительно:

- ноль silent overwrites;
- ноль отправленных аудио/JSON/TEX/PDF;
- ноль ложных teacher-approved revisions;
- ноль duplicate publications после retry/restart;
- все применимые CI/release checks зелёные либо оставшиеся failures документированы как внешние/несвязанные с feature.

---

## 28. Итоговая архитектура

```text
Quick Start UI
    ↓
persist Lesson.processing_mode
    ↓
RecordingWorkflow / StopRecordingUseCase
    ↓
finalized audio
    ↓
Persistent Transcription Queue
    ↓
ASR
    ↓
Immutable Automatic Transcript Revision (SQLite)
    ↓
Persistent Automatic Publication Queue
    ↓
Automatic Publication Authorization
    ↓
Shared Verified Git Transport
    ↓
PRIVATE GitHub / main
    ↓
<student>/transcript/DD.MM.YY.txt
```

Ключевая идея реализации: **persisted lesson mode → durable ASR → immutable transcript revision → durable publication intent → existing verified Git transport**. UI только запускает режим и отображает состояние; correctness, authorization, recovery и idempotency должны обеспечиваться application/persistence layers.
