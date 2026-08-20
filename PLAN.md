# Tutor Assistant — Development Plan

**Актуальность:** 19 августа 2026 года  
**Текущая версия:** `0.22.1`  
**Основная ветка:** `main`  
**Production entrypoint:** `tutor-assistant-gui -> tutor_assistant.ui.recording_recovery_app:main`

## 1. Текущее состояние

Tutor Assistant перешёл из фазы архитектурной стабилизации ядра в фазу **Production Readiness / Release 1.0 hardening**.

К текущему состоянию завершены:

- P0-стабилизация recording/data lifecycle;
- Wave 2 / Slices 1–12 — extraction production recording path из base Qt UI;
- Wave 3 / Slice 13 — transcription queue coordinator;
- Wave 3 / Slice 14 — normalization coordinator;
- Wave 3 / Slice 15 — LaTeX monitor coordinator;
- Wave 3 / Slice 16 — shutdown coordinator;
- Wave 3 / Slice 17 — Teacher Cockpit / recording-review workspace synchronization;
- P0 Production Safety Hardening — transactional restore boundary, single-channel audio recovery, recorder quiescence, observable lease loss, authoritative approved transcript publication, Git blob integrity и transcription reconciliation.

Последний объединённый production commit после Slice 17 + P0 hardening:

```text
4ffafbcda985e987419336e0f28baec3f1027140
```

Следующий архитектурный feature slice — **Wave 3 / Slice 18: Quick lesson launch orchestration / schedule bridge** — откладывается до завершения Release 1.0 hardening.

Главный текущий приоритет:

```text
стабильное ядро
    ↓
production runtime
    ↓
release governance
    ↓
crash/support observability
    ↓
automatic backup + disaster recovery
    ↓
Windows packaging
    ↓
release automation
    ↓
hardware soak
    ↓
Release Candidate
    ↓
v1.0.0
```

---

## 2. Архитектурная граница, которую нельзя нарушать

Production composition сохраняет принцип:

```text
Qt presentation / production composition adapters
                ↓
application use cases + coordinators + structural ports
                ↓
domain / pipeline services
                ↓
recording, persistence and external infrastructure
```

Базовые инварианты дальнейшей разработки:

- SQLite остаётся authoritative source of truth для локального архива и pipeline metadata;
- filesystem и `lesson.json` являются projections/artifacts, а не заменой транзакционной БД;
- audio-first recovery важнее metadata convenience;
- recovery не откатывает уже продвинувшийся lesson status;
- recording safety имеет приоритет над UX convenience;
- persisted transcription queue не очищается при обычном shutdown;
- cloud processing требует explicit consent;
- аудио не отправляется во внешние сервисы;
- application use cases/coordinators не должны зависеть от Qt;
- production UI остаётся renderer/router слоем, а не источником lifecycle policy;
- recording context и review context остаются независимыми;
- любое изменение recording/shutdown/backup/restore/release lifecycle сопровождается regression/architecture tests;
- пользовательские данные не должны попадать в release artifacts, CI artifacts или public repository.

---

# 3. Release 1.0 — цель

После завершения программы должна работать следующая цепочка:

```text
tag v1.0.0
      ↓
GitHub Actions
      ↓
required production tests
      ↓
Windows portable build
      ↓
Windows installer
      ↓
installation smoke test
      ↓
privacy scan / SHA-256 / manifest
      ↓
GitHub Release
      ↓
установка на чистую Windows
      ↓
first-run setup
      ↓
doctor
      ↓
реальный урок
```

При этом пользовательские данные должны переживать:

- обновление приложения;
- обычное завершение приложения;
- аварийное завершение;
- повреждение recording session;
- сбой фоновой задачи;
- повреждение live SQLite при наличии валидного backup;
- backup → restore;
- uninstall/reinstall приложения без удаления workspace;
- hardware/device disruption в пределах recoverable failure model.

Release 1.0 считается не просто сборкой, а первым эксплуатационным контрактом продукта.

---

# 4. Этап R1.0 — документировать Release 1.0 как текущую программу

## Цель

Сделать roadmap однозначным: Slice 17 и P0 hardening завершены, следующий текущий фокус — Release 1.0 Production Readiness.

## Изменения

- обновить `PLAN.md`;
- синхронизировать `README.md`;
- при реализации последующих этапов добавить специализированные документы:
  - `docs/OPERATIONS.md`;
  - `docs/INSTALLATION.md`;
  - `docs/RELEASE.md`;
  - `docs/DISASTER_RECOVERY.md`;
  - `docs/HARDWARE_SOAK.md`;
  - `docs/SUPPORT.md`;
  - `CHANGELOG.md`.

## Definition of Done

- roadmap не называет Slice 17 будущей работой;
- Release 1.0 hardening указан как текущая программа;
- Slice 18 явно перенесён после v1.0.0;
- все последующие PR получают номера R1.x и привязаны к данному плану.

---

# 5. Этап R1.1 — Python 3.12 как production runtime

## Цель

Убрать неопределённость runtime и исключить Python 3.11 из production support, сохранив возможность compatibility testing новых версий.

## Целевая support matrix

```text
Production packaged runtime: Python 3.12.x
Official source installation: Python >=3.12,<3.15
Compatibility CI: Python 3.13 / 3.14
Python 3.11: unsupported
```

## Реализация

1. Изменить `pyproject.toml`:

```toml
requires-python = ">=3.12,<3.15"
```

2. Добавить `.python-version`:

```text
3.12
```

3. Добавить runtime contract, например:

```text
src/tutor_assistant/runtime.py
```

с понятиями:

- production runtime;
- compatibility runtime;
- unsupported runtime.

4. Расширить `doctor`:

```text
Runtime
  Python: 3.12.x
  Production runtime: YES
```

Для 3.13/3.14:

```text
Production runtime: NO
Compatibility runtime: YES
```

5. Перестроить CI:

- обязательный production job — только Python 3.12;
- Python 3.13/3.14 — отдельный compatibility workflow/job;
- Python 3.11 удалить из matrix;
- удалить специальные 3.11-only process-isolation workarounds после подтверждения, что они больше не нужны.

## Acceptance criteria

- Python 3.11 исключён из официального support;
- Python 3.12 full suite green;
- production package собирается только Python 3.12;
- `doctor` показывает runtime status;
- README содержит runtime support matrix;
- dependency lock остаётся воспроизводимым.

---

# 6. Этап R1.2 — стабильный Release 1.0 CI Gate

## Цель

Создать один стабильный required status check, который можно использовать в branch protection независимо от внутренней структуры CI.

## Целевая схема

```text
production-py312 ─────┐
privacy ──────────────┤
architecture ─────────┤
accessibility ────────┤→ Release 1.0 Gate
package-smoke ────────┘
```

## Реализация

Добавить агрегирующий job с постоянным display name:

```text
Release 1.0 Gate
```

Он должен зависеть как минимум от:

1. `uv lock --check`;
2. Ruff;
3. compileall;
4. privacy import smoke;
5. secret scan для PR;
6. privacy contracts;
7. архитектурных contracts;
8. полного pytest на Python 3.12;
9. accessibility/scaling contracts;
10. packaging smoke после появления portable build.

Compatibility CI 3.13/3.14 не должен блокировать production release gate, если только отдельным решением поддержка этих версий не станет contractual.

## Acceptance criteria

- существует один stable required check `Release 1.0 Gate`;
- изменение matrix/job names не ломает branch protection;
- PR не может считаться release-ready при падении любого production-critical dependency job;
- failed/cancelled production job приводит к failed gate.

---

# 7. Repository setting — защита `main`

## Цель

Исключить попадание непроверенного кода в production branch.

## Настройки `main`

После первого успешного `Release 1.0 Gate` включить:

```text
Require a pull request before merging       ON
Require status checks                       ON
Require branches to be up to date           ON
Release 1.0 Gate                            REQUIRED
Require conversation resolution             ON
Block force pushes                          ON
Block branch deletion                       ON
```

Для single-maintainer repository не вводить обязательный approval второго человека, если это создаёт искусственную невозможность merge.

## Break-glass policy

Emergency bypass допускается только как административное исключение:

```text
emergency change
→ bypass only if production is blocked
→ post-factum PR / audit note
→ Release 1.0 Gate обязательно прогоняется
→ причина bypass фиксируется
```

## Acceptance criteria

- `main` protected;
- прямой force push запрещён;
- merge без required CI невозможен в штатном режиме;
- branch deletion запрещён;
- recovery/bypass процесс документирован.

---

# 8. Этап R1.3 — crash / support observability

## Фактическая база

Уже существуют:

- rotating `application.log`;
- redacting formatter;
- `SensitiveDataFilter`;
- `sys.excepthook`;
- privacy-safe support bundle;
- diagnostics/device inventory;
- исключение transcript/audio/cloud payload из support bundle.

Цель этапа — довести это до production crash observability, не создавая удалённую telemetry-систему.

## 8.1. Application session identity

На каждом запуске генерировать:

```text
application_session_id
```

Он должен присутствовать в структурированных log records и crash metadata.

## 8.2. Build identity

Логи/support bundle должны содержать:

- application version;
- Git commit SHA;
- release channel (`dev`, `rc`, `stable`);
- Python version;
- Windows version;
- architecture;
- `frozen/source` mode.

## 8.3. Crash marker

Создать безопасный файл:

```text
workspace/crash/last-crash.json
```

Пример разрешённых полей:

```json
{
  "timestamp": "...",
  "version": "1.0.0",
  "session_id": "...",
  "exception_type": "...",
  "component": "...",
  "recording_active": true,
  "transcription_active": false
}
```

Запрещено сохранять:

- transcript text;
- API keys/tokens;
- ФИО/контакты учеников;
- audio;
- LLM payloads;
- абсолютные пользовательские секретные пути, если они не прошли sanitation.

## 8.4. Exception boundaries

Покрыть:

- `sys.excepthook`;
- `threading.excepthook`;
- Qt message handler;
- background worker boundary;
- `faulthandler` для полезных native-level diagnostics там, где это безопасно.

## 8.5. UX после crash

При следующем запуске, если обнаружен crash marker:

```text
Предыдущий запуск завершился аварийно

[Создать пакет диагностики]
[Открыть журнал]
[Продолжить]
```

Это не должно блокировать normal startup и не должно автоматически отправлять данные.

## 8.6. Support bundle v2

Добавить безопасные entries:

```text
crash/last-crash.json
build-info.json
backup-status.json
workspace-health.json
```

## Tests

Обязательные сценарии:

- secret in exception → redacted;
- secret in Qt message → redacted;
- thread crash → logged;
- crash marker created;
- crash marker contains no sensitive payload;
- support bundle содержит crash/build metadata;
- support bundle не содержит transcript/audio/API key;
- support bundle generation itself не падает из-за malformed log entry.

## Definition of Done

- любой Python-level unhandled crash оставляет локально диагностируемый след;
- crash metadata privacy-safe;
- support bundle пригоден для разбирательства без ручного сбора файлов;
- никакой автоматической внешней отправки telemetry нет.

---

# 9. Этап R1.4 — automatic backup scheduler + retention

## Фактическая база

Уже существуют настройки:

```text
backup_enabled = true
backup_interval_hours = 24
backup_retention_count = 14
```

и `DatabaseBackupStore` с операциями:

```text
create
verify
list
prune
restore_from
restore_offline
```

с SHA-256, SQLite `quick_check` и `foreign_key_check`.

Следовательно, этап не создаёт новый backup engine. Он создаёт **production scheduler и operational guarantees** поверх уже существующей безопасной базы.

## 9.1. Qt-free coordinator

Создать:

```text
application/backup_maintenance.py
```

Основные типы:

```text
BackupMaintenanceCoordinator
BackupMaintenanceSnapshot
BackupDecision
```

Coordinator определяет:

- включены ли backups;
- когда был последний successful scheduled backup;
- наступил ли interval;
- выполняется ли уже backup;
- разрешено ли начинать backup в текущем lifecycle;
- требуется ли retention;
- результат последнего create/verify/prune.

QTimer/background transport не должен содержать policy.

## 9.2. Startup scheduling

При запуске:

```text
найти последнюю successful scheduled backup
        ↓
старше backup_interval_hours?
        ↓
YES → enqueue background backup
```

## 9.3. Periodic scheduling

```text
maintenance timer
→ coordinator
→ due?
→ create backup
→ verify backup
→ retention
→ persist status
```

## 9.4. Safety barriers

Не начинать новый scheduled backup в критические моменты:

- recording start transaction;
- recording stop/finalize;
- database restore;
- destructive/repair maintenance;
- shutdown draining, если нет гарантии безопасного завершения backup.

## 9.5. Create → verify → retention

Только такой порядок:

```text
create
  ↓
verify
  ↓
valid?
  ↓ yes
prune
```

Если create/verify failed — старые backups не удаляются.

## 9.6. Backup reasons

Использовать явные reasons:

```text
scheduled
manual
pre-restore-safety
pre-upgrade
```

Retention policy должна различать классы backup. Обычный scheduled prune не должен случайно уничтожать единственную safety copy, созданную перед restore/upgrade.

## 9.7. Operational status

В diagnostics/Teacher Cockpit или отдельном maintenance view показывать:

```text
Последняя копия: 19.08.2026 18:00
Статус: проверена
Следующая: 20.08.2026
Scheduled copies: 14
Последняя ошибка: —
```

Ошибки backup показываются ненавязчиво, но остаются видимыми до следующего successful cycle.

## Tests

- due/not-due decisions;
- backup disabled;
- recording barrier;
- duplicate/single-flight protection;
- create fail → no prune;
- verify fail → no prune;
- success → prune scheduled copies only;
- safety backups preserved;
- restart correctly restores last backup status;
- shutdown не оставляет ложный successful state.

## Definition of Done

- backup создаётся автоматически;
- backup проверяется автоматически;
- retention автоматизирован;
- failed backup никогда не приводит к удалению последних валидных копий;
- пользователь видит возраст последней валидной копии.

---

# 10. Этап R1.5 — Disaster Recovery Drill

## Цель

Проверять не отдельные методы backup/restore, а реальную способность продукта восстановиться после контролируемого отказа.

## Команда

Добавить:

```text
tutor-assistant recovery-drill
```

Drill никогда не должен изменять live workspace.

## 10.1. Sandbox workflow

```text
live workspace
      ↓
read-only fingerprint
      ↓
temporary isolated sandbox
      ↓
backup
      ↓
verify
      ↓
controlled mutations
      ↓
restore
      ↓
restart StudentContentService
      ↓
content integrity validation
      ↓
filesystem reconciliation
      ↓
recording recovery scenarios
      ↓
JSON report
```

## 10.2. Backup checks

До restore проверить:

- manifest readable;
- SHA-256 matches;
- SQLite quick_check = `ok`;
- foreign_key_check clean;
- schema version known;
- backup file не изменяется во время verification.

## 10.3. Post-backup lesson quarantine

В sandbox:

1. создать backup;
2. после backup создать lesson B/filesystem directory;
3. выполнить restore backup;
4. подтвердить:
   - lesson B отсутствует в restored authoritative DB;
   - физические данные lesson B не уничтожены;
   - lesson B перенесён в restore quarantine;
   - manifest quarantine корректен.

## 10.4. Recording recovery drill

Synthetic scenarios:

```text
A. mic + system chunks      → canonical recovery
B. mic only                 → degraded but recoverable
C. system only              → degraded but recoverable
D. neither channel          → explicit unrecoverable
```

## 10.5. Fault-injection

Проверить как минимум:

- restore projection failure;
- rollback-to-safety DB;
- malformed manifest;
- SHA mismatch;
- corrupted SQLite backup;
- quarantine restore collision;
- partial recording metadata corruption.

## 10.6. Report

Создавать privacy-safe JSON:

```text
recovery-drill-YYYYMMDDTHHMMSS.json
```

Пример:

```text
Database backup       PASS
Backup verification   PASS
Restore               PASS
Filesystem projection PASS
Quarantine            PASS
Rollback safety       PASS
Mic-only recovery     PASS
System-only recovery  PASS
Content doctor        PASS
```

## Release requirement

Перед `v1.0.0` recovery drill должен завершаться PASS на release candidate build.

---

# 11. Этап R1.6 — Windows portable build

## Цель

Получить self-contained Windows application, не требующий ручной установки Python/uv пользователем.

## Packaging strategy

Для Release 1.0 использовать:

```text
PyInstaller onedir
```

а не `onefile`, потому что production stack включает:

- PySide6/Qt;
- native audio dependencies;
- SoundCard/sounddevice;
- CTranslate2/faster-whisper;
- потенциально крупные native DLL dependencies.

## 11.1. Файлы

Добавить:

```text
packaging/windows/tutor-assistant.spec
scripts/build_windows.py
tests/test_packaging_contract.py
```

## 11.2. Portable layout

```text
dist/
└── TutorAssistant/
    ├── TutorAssistant.exe
    ├── runtime DLLs
    ├── Qt plugins
    ├── config/
    │   └── app.example.yaml
    └── ...
```

Архив:

```text
TutorAssistant-<version>-win64-portable.zip
```

## 11.3. User data boundary

Program files и user data должны быть физически разделены.

Installer mode:

```text
Application:
%LOCALAPPDATA%\Programs\TutorAssistant

Configuration:
%APPDATA%\TutorAssistant

Workspace:
пользовательский путь или безопасный default вне Program Files
```

Portable mode допускает локальные:

```text
./config
./data
./logs
```

только при явном portable marker/mode.

## 11.4. Privacy packaging gate

Release build должен fail, если artifact содержит:

- `config/app.yaml` с реальными настройками;
- `config/students.yaml`;
- `.env`;
- пользовательские SQLite;
- `data/`;
- реальные audio/transcript artifacts;
- credentials/tokens;
- приватные support bundles.

Build выполняется только из clean checkout.

## 11.5. Optional dependencies

Не включать автоматически:

- TeX Live;
- Ollama;
- LLM models;
- Whisper/GigaAM model weights, если это не будет отдельным осознанным distribution decision.

FFmpeg должен иметь явный deployment contract:

- либо controlled bundled binary с license/manifest;
- либо гарантированный основной workflow без обязательного FFmpeg;
- либо doctor/setup должен однозначно сообщать о required dependency.

## 11.6. Portable smoke

На CI после build:

```text
launch executable in diagnostic/headless-safe mode
→ version
→ doctor subset
→ import production entrypoint
→ verify no missing DLL/plugin
→ exit 0
```

## Acceptance criteria

- приложение запускается на clean Windows runner без system Python;
- production entrypoint используется и во frozen build;
- user data не пишутся в Program Files;
- portable artifact не содержит приватные файлы;
- executable build reproducibly создаётся из tag/commit.

---

# 12. Этап R1.7 — Windows installer

## Цель

Сделать нормальную установку, обновление и удаление приложения без потери пользовательских данных.

## Installer

Использовать Inno Setup:

```text
packaging/windows/TutorAssistant.iss
```

Installer должен:

- устанавливать application files;
- создавать Start Menu entry;
- опционально desktop shortcut;
- регистрировать uninstall;
- не перезаписывать пользовательский config при update;
- не удалять workspace при uninstall;
- корректно обновлять существующую installation.

## Upgrade scenario

Обязательный test flow:

```text
install rc build A
↓
создать config/workspace/test data
↓
install rc build B поверх A
↓
config preserved
↓
SQLite preserved
↓
lessons preserved
↓
application starts
```

Перед будущими schema/application upgrades использовать backup reason:

```text
pre-upgrade
```

## Uninstall scenario

После uninstall:

```text
application files removed
workspace preserved
backups preserved
config preserved
```

Удаление пользовательских данных разрешено только отдельным явным действием пользователя.

## Acceptance criteria

- clean install работает;
- repair/reinstall не ломает workspace;
- update preserves data;
- uninstall preserves data;
- installer artifact проходит privacy scan.

---

# 13. Этап R1.8 — автоматизация release artifacts

## Workflow

Добавить:

```text
.github/workflows/release.yml
```

Triggers:

```text
workflow_dispatch
push tag: v1.*
```

## Release pipeline

```text
checkout
  ↓
Python 3.12
  ↓
uv lock --check
  ↓
Release 1.0 Gate
  ↓
PyInstaller onedir
  ↓
portable smoke
  ↓
Inno Setup
  ↓
installer smoke
  ↓
artifact privacy scan
  ↓
SHA-256
  ↓
build manifest
  ↓
GitHub Release
```

## Release artifacts

Для stable release:

```text
TutorAssistant-1.0.0-win64-setup.exe
TutorAssistant-1.0.0-win64-portable.zip
TutorAssistant-1.0.0-py3-none-any.whl
SHA256SUMS.txt
build-manifest.json
```

Опционально:

```text
SBOM
```

## Build manifest

Минимальный состав:

```json
{
  "version": "1.0.0",
  "commit": "...",
  "python": "3.12.x",
  "platform": "windows-x64",
  "build_type": "release"
}
```

## Version consistency gate

Перед publish:

```text
Git tag
== package version
== tutor_assistant.__version__
== installer version
== build manifest version
```

Любое несовпадение → release fail.

## Immutability

Stable release artifacts не должны silently заменяться. Исправление после публикации делается новым patch release (`1.0.1`), а не заменой байтов `1.0.0`.

---

# 14. Этап R1.9 — code signing

## Цель

Снизить предупреждения Windows и обеспечить проверяемое происхождение release binaries.

Для внутренних RC подпись может быть необязательной. Для распространения stable `1.0.0` другим пользователям желательно подписывать:

```text
TutorAssistant.exe
TutorAssistant-<version>-win64-setup.exe
```

Pipeline:

```text
build
↓
sign
↓
verify signature
↓
SHA-256
↓
publish
```

Requirements:

- signing secret/certificate private key не хранится в repository;
- CI logs не выводят sensitive signing material;
- unsigned artifact не может быть случайно маркирован как signed stable artifact;
- signature verification входит в release report.

---

# 15. Этап R1.10 — Hardware Soak Framework

## Цель

Компенсировать принципиальное ограничение обычного GitHub Actions: offscreen Windows CI не проверяет реальный WASAPI/device lifecycle.

## Harness

Добавить команду или script:

```text
tutor-assistant hardware-soak
```

или:

```text
scripts/hardware_soak.py
```

## Privacy-safe metrics

Собирать без аудиосодержимого:

- device stable ID/fingerprint;
- backend;
- sample rate;
- runtime duration;
- captured block count;
- dropped blocks;
- queue high-water mark;
- writer latency;
- reconnect count;
- silence periods;
- stream exceptions;
- process memory trend;
- finalize duration;
- output sizes;
- quality/recovery classification;
- application version/commit.

Не сохранять в repository реальные recordings.

---

# 16. Этап R1.10 — обязательные hardware scenarios

## A. Long recording

```text
>= 2 часа непрерывной записи
mic + system audio
```

PASS:

- no application crash;
- canonical `lesson.wav` readable;
- source tracks readable;
- session finalized;
- DB consistent;
- no stuck recorder/lease;
- dropped-block metrics в допустимом диапазоне.

## B. Repeated lifecycle

Минимум:

```text
20 × start → record → stop
```

Проверять:

- resource leak;
- stuck lease;
- writer thread leak;
- device handle leak;
- cumulative memory growth;
- finalize failures.

## C. Microphone disconnect/reconnect

Проверить:

- before preflight;
- during preflight;
- during recording;
- after recording;
- reindex после reconnect.

## D. Playback endpoint change

```text
Playback endpoint A
→ Windows default endpoint changes
→ endpoint B
```

Проверить discovery/recovery/error presentation.

## E. Forced process termination

Не менее 5 раз:

```text
recording active
↓
force process kill
↓
restart
↓
recovery
```

Доступные chunks должны восстанавливаться согласно failure model.

## F. Single-channel degradation

```text
mic disappears, system survives
```

и:

```text
system disappears, mic survives
```

Оба случая должны давать recoverable canonical audio, если surviving chunks пригодны.

## G. Parallel review

```text
recording student A
+
review student B
+
Teacher Cockpit
```

Проверить:

- stop controls всё ещё относятся к A;
- review controls относятся к B;
- playback B заблокирован во время recording busy;
- recording form не подменяется lesson B.

## H. Background workload

Проверить recording во время:

- scheduled backup decision;
- content maintenance;
- completed transcription processing;
- Teacher Cockpit refresh;
- normalization queue activity, где разрешено policy.

Backup/maintenance должны уступать safety-critical recording transitions.

---

# 17. Hardware acceptance report

Хранить только sanitized evidence, например:

```text
release-evidence/
└── v1.0.0-rc1/
    └── hardware-soak.json
```

Перед stable `1.0.0` целевые минимумы:

```text
>= 20 часов cumulative recording
>= 1 непрерывная session 2 часа
>= 20 start/stop cycles
>= 5 forced crash/recovery cases
>= 5 device disruption cases
0 lost recoverable recordings
0 unexplained unhandled application crashes
```

Любой failure, который приводит к потере recoverable recording, блокирует stable release до root-cause/fix/retest.

---

# 18. Этап R1.10/R1.11 — Operations documentation

## README.md

Сделать пользовательским first-entry документом:

1. что делает Tutor Assistant;
2. Download/Install;
3. First Run;
4. Setup/Doctor;
5. Record lesson;
6. Recover recording;
7. Transcribe/review;
8. Backup/restore;
9. Troubleshooting;
10. links на deeper docs.

Архитектурную историю не держать в первом пользовательском экране README.

## PLAN.md

Остаётся roadmap/status source of truth.

## Дополнительные docs

### `docs/INSTALLATION.md`

- installer;
- portable mode;
- directories;
- external optional dependencies;
- upgrade/uninstall.

### `docs/OPERATIONS.md`

- daily startup;
- doctor;
- preflight;
- backup status;
- recovery cues;
- logs;
- safe shutdown.

### `docs/DISASTER_RECOVERY.md`

- backup verification;
- restore;
- offline restore;
- quarantine;
- recording recovery;
- manual escalation.

### `docs/HARDWARE_SOAK.md`

- physical test matrix;
- result schema;
- pass/fail thresholds.

### `docs/SUPPORT.md`

- crash marker;
- support bundle;
- privacy guarantees;
- what user may safely send for diagnosis.

### `docs/RELEASE.md`

- RC/stable workflow;
- tagging;
- checksums;
- signing;
- release rollback/patch policy.

### `CHANGELOG.md`

Следовать понятной human-readable версии изменений по релизам.

---

# 19. Release Candidate process

После завершения implementation PR:

```text
main green
↓
tag v1.0.0-rc.1
↓
release workflow
↓
portable + installer
↓
clean Windows installation
↓
recovery drill
↓
hardware soak
↓
несколько реальных рабочих дней
```

Production defect:

```text
fix/*
→ PR
→ Release 1.0 Gate
→ merge
→ v1.0.0-rc.2
```

Stable release создаётся только после успешного RC cycle.

---

# 20. Предлагаемое разбиение на PR

| PR | Содержание | Зависимость |
|---|---|---|
| **R1.0** | актуализировать `PLAN.md`/roadmap и зафиксировать Release 1.0 program | — |
| **R1.1** | Python 3.12 production contract + CI restructuring | R1.0 |
| **R1.2** | stable `Release 1.0 Gate` | R1.1 |
| **Repo setting** | branch protection `main` | R1.2 |
| **R1.3** | crash logging + crash marker + support bundle v2 | R1.2 |
| **R1.4** | automatic backup scheduler + retention policy | R1.2 |
| **R1.5** | disaster recovery drill | R1.4 |
| **R1.6** | PyInstaller portable build | R1.3 |
| **R1.7** | Inno Setup installer + upgrade/uninstall smoke | R1.6 |
| **R1.8** | automated GitHub release workflow | R1.7 |
| **R1.9** | code-signing integration | R1.8 / certificate availability |
| **R1.10** | hardware soak harness + evidence format | R1.3 + R1.4 |
| **R1.11** | docs, CHANGELOG, version `1.0.0-rc.1` | все implementation PR |
| **R1.12** | final release fixes / `v1.0.0` | successful RC soak |

Не объединять packaging, backup semantics, crash handling и release automation в один большой PR: это разные failure domains и должны иметь отдельные review/rollback boundaries.

---

# 21. Critical path

Рекомендуемый фактический порядок:

```text
R1.0 Documentation baseline
      ↓
R1.1 Python 3.12 runtime
      ↓
R1.2 Release Gate
      ↓
Protect main
      ↓
┌─────────────────────┬──────────────────────┐
│ R1.3 Crash/support  │ R1.4 Auto backups    │
└──────────┬──────────┴───────────┬──────────┘
           │                      ↓
           │                 R1.5 Recovery drill
           ↓
      R1.6 Portable build
           ↓
      R1.7 Installer
           ↓
      R1.8 Release workflow
           ↓
      R1.9 Signing
           ↓
      R1.10 Hardware soak
           ↓
      R1.11 RC documentation/version
           ↓
      v1.0.0-rc.1
           ↓
      soak + real-use validation
           ↓
      v1.0.0
```

R1.3 и R1.4 могут разрабатываться независимо после стабилизации CI gate.

---

# 22. Risk register Release 1.0

## Risk A — frozen application и native DLL

**Вероятность:** средняя.  
**Влияние:** высокое.

Mitigation:

- PyInstaller `onedir`;
- clean Windows smoke;
- explicit DLL/plugin validation;
- Python 3.12 single production runtime.

## Risk B — audio hardware отличается от CI

**Вероятность:** высокая.  
**Влияние:** критическое.

Mitigation:

- hardware soak harness;
- forced disconnect/reconnect scenarios;
- long recording;
- repeated start/stop;
- real lessons during RC.

## Risk C — automatic backup создаёт I/O contention во время recording

**Вероятность:** средняя.  
**Влияние:** высокое.

Mitigation:

- coordinator policy;
- recording barriers;
- background scheduling;
- no prune after failed backup;
- soak test с backup due во время active lesson.

## Risk D — support/crash logging утечёт sensitive data

**Вероятность:** низкая/средняя.  
**Влияние:** критическое.

Mitigation:

- redaction at formatter/filter boundary;
- allowlist crash metadata;
- privacy tests;
- artifact/support-bundle secret scans;
- no transcript/audio by default.

## Risk E — update/uninstall затронет workspace

**Вероятность:** средняя до тестирования.  
**Влияние:** критическое.

Mitigation:

- strict program-data separation;
- installer upgrade smoke;
- uninstall preservation test;
- pre-upgrade backup.

## Risk F — release workflow опубликует artifact не из того commit/tag

**Вероятность:** низкая.  
**Влияние:** высокое.

Mitigation:

- version consistency gate;
- build manifest;
- SHA-256;
- immutable release policy;
- commit SHA in support/build info.

---

# 23. Definition of Done — Release 1.0

Stable `v1.0.0` разрешён только если выполнены все пункты:

## Runtime / CI

- [ ] production runtime = Python 3.12;
- [ ] Python 3.11 removed from official support;
- [ ] Python 3.12 full suite green;
- [ ] `Release 1.0 Gate` существует и стабилен;
- [ ] privacy gate green;
- [ ] accessibility/scaling gate green;
- [ ] package smoke green;
- [ ] `main` protected;
- [ ] force push to `main` blocked.

## Crash / Support

- [ ] application session ID реализован;
- [ ] build identity доступен в logs/support bundle;
- [ ] crash marker реализован;
- [ ] threading/Qt/background exception boundaries покрыты;
- [ ] support bundle v2 privacy-safe;
- [ ] secrets/transcripts/audio не попадают в support bundle.

## Backup / Recovery

- [ ] automatic scheduled backup работает;
- [ ] backup verification автоматизирован;
- [ ] retention работает;
- [ ] failed backup не удаляет старые валидные copies;
- [ ] safety backup classes защищены от обычного prune;
- [ ] disaster recovery drill PASS;
- [ ] restore quarantine PASS;
- [ ] rollback-to-safety DB PASS;
- [ ] mic-only recovery PASS;
- [ ] system-only recovery PASS.

## Packaging / Installation

- [ ] portable ZIP builds;
- [ ] installer builds;
- [ ] clean Windows install smoke passes;
- [ ] source Python не требуется конечному пользователю;
- [ ] update preserves workspace/config;
- [ ] uninstall preserves workspace/config/backups;
- [ ] release artifacts не содержат private data;
- [ ] version/tag/build manifest agree.

## Release

- [ ] release workflow создаёт artifacts автоматически;
- [ ] SHA-256 checksums published;
- [ ] build manifest published;
- [ ] signing выполнен или явно задокументирован как release exception;
- [ ] release notes/CHANGELOG актуальны.

## Physical validation

- [ ] cumulative hardware soak >= 20 часов;
- [ ] хотя бы одна continuous recording >= 2 часов;
- [ ] >= 20 start/stop cycles;
- [ ] >= 5 forced crash/recovery tests;
- [ ] >= 5 device disruption tests;
- [ ] parallel recording/review scenario passes;
- [ ] scheduled backup interaction with recording passes;
- [ ] 0 lost recoverable recordings;
- [ ] 0 unexplained unhandled application crashes.

## Documentation

- [ ] README соответствует Release 1.0;
- [ ] INSTALLATION актуален;
- [ ] OPERATIONS актуален;
- [ ] DISASTER_RECOVERY актуален;
- [ ] HARDWARE_SOAK актуален;
- [ ] SUPPORT актуален;
- [ ] RELEASE актуален;
- [ ] PLAN отмечает Release 1.0 как DONE после stable tag.

---

# 24. Release readiness decision

Формальный release decision принимается по правилу:

```text
Release 1.0 Gate PASS
AND recovery drill PASS
AND packaging/install smoke PASS
AND hardware acceptance PASS
AND no open P0/P1 data-loss defects
AND documentation current
→ разрешён v1.0.0
```

Если остаётся известный defect, способный привести к:

- потере recoverable audio;
- повреждению authoritative SQLite;
- silent publication неправильного transcript revision;
- утечке sensitive data;
- уничтожению workspace при install/update/uninstall;

stable release блокируется независимо от количества зелёных unit tests.

---

# 25. После Release 1.0

Только после stable `v1.0.0` возобновить feature architecture roadmap.

## Wave 3 / Slice 18 — Quick lesson launch orchestration / schedule bridge

Цель: вынести quick-lesson lifecycle из widget flags/QTimer policy в Qt-free application coordinator, не дублируя `RecordingWorkflowController`.

Планируемая boundary:

```text
QuickLessonCoordinator
  readiness
  → preflight
  → countdown
  → recording request
  → active observation
  → stop/finalize observation
  → post-recording decision
```

Coordinator должен владеть:

- lifecycle phases;
- scheduled occurrence context;
- countdown cancellation decision;
- auto-transcribe decision;
- typed side-effect actions.

QTimer остаётся transport mechanism.

Не возвращать source-of-truth policy в legacy flags:

```text
_quick_start_pending
_quick_auto_transcribe_active
_quick_countdown_remaining
_scheduled_occurrence_id
```

Сохранить:

- F9;
- quick start button;
- recording safety;
- schedule linkage;
- parallel review invariants;
- текущий визуальный UX, если отдельный redesign не запланирован.

---

# 26. Рабочий порядок для каждого Release 1.0 PR

Для каждого R1.x:

1. зафиксировать фактический `main` head;
2. сформулировать failure model и scope;
3. определить testable application/infrastructure boundary;
4. добавить regression tests до или вместе с production switch;
5. не смешивать соседние failure domains;
6. запускать targeted tests;
7. запускать production Python 3.12 gate;
8. проверять privacy implications;
9. создавать PR;
10. merge только после required checks;
11. после merge обновлять status/roadmap, если этап завершён.

Главный принцип Release 1.0:

> Не добавлять новые пользовательские функции, пока приложение не получит воспроизводимый runtime, защищённый release process, наблюдаемость отказов, автоматический backup, доказанный disaster recovery, Windows packaging и физически проверенную надёжность записи.
