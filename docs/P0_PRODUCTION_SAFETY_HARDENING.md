# P0 Production Safety Hardening

**Дата:** 19 августа 2026 года  
**База:** `e7438eb3683adf51afbe5bf112ce1afef041bb49` (head PR #101)  
**Рабочая ветка:** `agent/p0-production-safety-hardening`

## Цель

Закрыть failure modes, способные привести к потере физически сохранённого аудио, split-brain между SQLite и файловой проекцией, снятию cross-process защиты до фактической остановки recorder, публикации неподтверждённого текста или повторной дорогостоящей транскрибации после частичного успеха.

## P0-1. Transactional database + filesystem restore

### Проблема

Текущий restore откатывает SQLite safety-backup после исключения, но filesystem side effects, выполненные `recover_trash_operations()` и `_synchronize_lesson_files()`, могут остаться от восстанавливаемой копии.

### Решение

- создать filesystem safety snapshot только для managed mutable roots (`lessons`, `.trash`) перед первым filesystem side effect;
- выполнять database restore, migrations и projection в staging/guarded restore scope;
- при любой ошибке восстановить **и SQLite, и managed filesystem roots**;
- generation increment выполнять только после полного успешного commit;
- не включать `backups/` и operations DB в filesystem snapshot, чтобы rollback не уничтожил safety-backup и lease state.

### Regression criteria

Fault injection после database replacement, migrations, trash recovery и после первого lesson projection должна оставлять DB и managed files byte-for-byte в исходном состоянии.

## P0-2. Audio-first single-channel recovery

### Проблема

Recovery требует одновременно пригодных microphone и system chunks. При полной потере одного канала второй канал, даже если полностью читаем, не становится delivery master.

### Решение

- `mic + system` → текущий sync/mix path;
- `mic only` → canonical `lesson.wav` из microphone track;
- `system only` → canonical `lesson.wav` из system track;
- `none` → unrecoverable error;
- `sync_report.json` должен явно фиксировать recovery mode и missing source;
- quality report должен уметь описывать degraded single-source recovery без попытки открыть отсутствующий track.

### Regression criteria

Отдельные mic-only/system-only tests, idempotent second recovery и damaged-other-track case.

## P0-3. Recorder quiescence before lease release

### Проблема

`recorder.stop()` может завершиться ошибкой после timeout/ошибки stream/writer, но recorder становится inactive, а `StopRecordingUseCase` освобождает recording lease в `finally` независимо от фактической quiescence.

### Решение

- ввести явный recorder contract `quiesced`;
- stop/abort выполняют best-effort shutdown всех streams/writers с агрегированием ошибок;
- `_active=False` допустим только после отсутствия живых capture/writer threads;
- recording lease освобождается только при подтверждённой quiescence;
- при `RECOVERY_REQUIRED` + non-quiesced recorder lease остаётся удержанным до explicit cleanup/restart процесса.

### Regression criteria

Writer timeout, stream close failure и partial-start failure не должны позволять новый destructive/exclusive operation, пока recorder не quiesced.

## P0-4. Observable lease loss

### Проблема

Heartbeat thread может прекратиться после `heartbeat()==False`/SQLite exception, а owner продолжит считать lease действующим.

### Решение

- lease state: `HEALTHY / LOST / RELEASED`;
- heartbeat exception не должен бесшумно завершать protection;
- `_current_thread_lease_protects()` учитывает только healthy lease;
- write path fail-closed при owned lost lease;
- expose `lease.valid` / `lease.lost_reason` для application/UI diagnostics.

### Regression criteria

Simulated heartbeat rejection/exception должен переводить lease в LOST и запрещать protected write path без reacquire.

## P0-5. Approved transcript revision as publication authority

### Проблема

Статус `READY` подтверждает workflow, но publisher читает mutable filesystem projection. Изменение файла после approval может привести к публикации байтов, которых преподаватель не подтверждал.

### Решение

- перед publication получать authoritative current `TranscriptRevision` из SQLite;
- expected approved SHA должен совпадать с disk projection;
- предпочтительно передавать в publisher immutable approved payload (`content`, `sha256`, revision number), а не перечитывать произвольный path;
- любое расхождение → fail closed и возврат в review/repair flow, без push.

### Regression criteria

После approval заменить `transcript_verified.txt`; publication обязана завершиться до git mutation с integrity error.

## P0-6. Git staged/committed blob integrity

### Проблема

Path-only egress guard не гарантирует, что staged/committed blob совпадает с approved payload: clean filters/hooks могут изменить тот же разрешённый `transcript.txt`.

### Решение

- после `git add` вычислить SHA-256 содержимого `git show :<path>` и сравнить с approved payload;
- после commit повторить проверку `HEAD:<path>`;
- publication commit выполнять с controlled hooks policy (`core.hooksPath` на пустой managed каталог либо `-c core.hooksPath=...`);
- remote compare-and-swap использовать exact expected SHA.

### Regression criteria

Repository с `.gitattributes` clean filter / commit hook не может silently изменить approved payload.

## P1-1. Transcription persistence reconciliation

### Проблема

ASR и artifact write могут завершиться, но финальная SQLite transition в `REVIEW_REQUIRED` — нет. После restart состояние остаётся `TRANSCRIBING` и работа может выполняться повторно.

### Решение

- добавить durable intermediate/reconciliation marker либо проверяемый transcription manifest;
- retry сначала проверяет существующие валидные artifacts/manifest и завершает state transition без повторного ASR;
- artifact write должен быть atomic относительно manifest publication.

### Regression criteria

Fault injection после artifact generation, но до final state save, при retry не вызывает transcriber второй раз.

## P1 hardening

После P0/P1-1:

- legacy publication path collision;
- transcript relative path invariant `lessons/<lesson_id>/...`;
- recursive LaTeX dependency validation;
- exact remote ref CAS/force-with-lease semantics;
- architecture gates, запрещающие обход новых safety boundaries.

## Порядок реализации

1. P0-2 + P0-3: сначала защита физического аудио и recorder lifecycle.
2. P0-4: затем cross-process lease semantics.
3. P0-1: transactional restore поверх исправленного lease contract.
4. P0-5 + P0-6: publication integrity boundary.
5. P1-1: transcription reconciliation.
6. P1 hardening и документация.
7. Targeted tests → full test suite → Windows matrix → privacy/history gates.

## Definition of Done

- ни один recorder failure path не удаляет последнюю физически пригодную копию аудио;
- exclusive/destructive operation не может стартовать при реально живом recorder;
- restore либо полностью применяет DB+managed filesystem state, либо полностью возвращает исходное состояние;
- published transcript byte-for-byte соответствует approved SQLite revision;
- ASR не повторяется после доказуемого успешного artifact stage;
- новые failure modes покрыты fault-injection tests;
- existing state-machine, privacy, publication egress и production MRO contracts сохранены.
