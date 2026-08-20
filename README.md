# Tutor Assistant

Текущая версия: **1.0.0rc1** · Production Readiness / Release 1.0.

Tutor Assistant — локальное Windows-приложение для полного цикла работы преподавателя после занятия:

```text
подготовка занятия
→ запись микрофона + системного звука
→ безопасное сохранение и восстановление аудио
→ локальная транскрибация faster-whisper
→ ручная проверка и LLM-фильтрация учебного содержания
→ публикация подтверждённого транскрипта
→ LaTeX / PDF / web-материалы
```

Главный принцип проекта — **local first**. Аудиозаписи, локальная SQLite-база, CRM и рабочие файлы преподавателя остаются на компьютере. В ученический репозиторий передаются только явно подготовленные артефакты публикации.

Подробный roadmap и условия выхода stable release: [`PLAN.md`](PLAN.md).

## Установка и поддерживаемый runtime

Windows installer устанавливает приложение в `%LOCALAPPDATA%\Programs\TutorAssistant`; отдельная
установка Python конечному пользователю не требуется. Portable ZIP запускается рядом с явным
маркером `portable.mode`. До публикации release artifacts доступна установка из исходников:

```powershell
git clone https://github.com/ArtemLevin/tutor-assistant.git
cd tutor-assistant
uv sync --extra desktop --extra transcription --group dev
uv run python scripts\bootstrap.py
```

| Runtime | Поддержка |
| --- | --- |
| Python 3.12.x | Production runtime и обязательная release-сборка |
| Python 3.13 / 3.14 | Compatibility testing; не блокирует production gate |
| Python 3.11 и ниже | Не поддерживается |

## Первый запуск и проверка окружения

```powershell
uv run tutor-assistant-gui config\app.yaml
uv run tutor-assistant --config config\app.yaml doctor
```

Мастер первого запуска помогает выбрать рабочий каталог, устройства записи и параметры урока.
Команда `doctor` показывает production/compatibility runtime, доступность аудио и статус
проверенной резервной копии. Для GitHub-публикации `gh` CLI не обязателен.

## Запись, восстановление и обработка урока

1. Выберите ученика, предмет, тему и проверьте микрофон/системное аудио.
2. Начните или завершите запись основной кнопкой либо клавишей `F9`.
3. При аварийном завершении восстановите доступные WAV-чанки при следующем запуске.
4. Запустите локальную транскрибацию, проверьте текст и подтвердите его перед публикацией.
5. Для облачной фильтрации требуется отдельное согласие; аудио наружу не отправляется.

## Резервирование, восстановление и диагностика

```powershell
uv run tutor-assistant --config config\app.yaml content-backup --create
uv run tutor-assistant --config config\app.yaml content-backup --verify PATH.sqlite3
uv run tutor-assistant --config config\app.yaml recovery-drill --output ..\recovery-report.json
uv run tutor-assistant --config config\app.yaml support-bundle
uv run tutor-assistant --config config\app.yaml hardware-soak
```

Плановые копии обязательно проверяются и очищаются отдельно от protective
`pre-restore-safety`/`pre-upgrade` copies. `recovery-drill` работает только с синтетической
песочницей и не изменяет реальное рабочее пространство. Support bundle не включает аудио,
транскрипты или API-ключи.

## Эксплуатационная документация

- [`docs/INSTALLATION.md`](docs/INSTALLATION.md) — installer, portable, каталоги и обновление.
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — ежедневная работа, doctor, backup и shutdown.
- [`docs/DISASTER_RECOVERY.md`](docs/DISASTER_RECOVERY.md) — restore, quarantine и recovery drill.
- [`docs/SUPPORT.md`](docs/SUPPORT.md) — crash marker, безопасный журнал и support bundle.
- [`docs/HARDWARE_SOAK.md`](docs/HARDWARE_SOAK.md) — физические сценарии и release thresholds.
- [`docs/RELEASE.md`](docs/RELEASE.md) — CI gate, защита main, packaging и signing.
- [`CHANGELOG.md`](CHANGELOG.md) — изменения по версиям.

## Текущее состояние архитектуры

**Срез:** 20 августа 2026 года.

Production GUI запускается через console entrypoint:

```text
tutor-assistant-gui
→ tutor_assistant.ui.recording_recovery_app:main
```

Не используйте `python -m tutor_assistant.ui.app` как production entrypoint. `ui/app.py` теперь является общим presentation shell и набором command ports; safety-critical recording orchestration реализована application use cases и production adapters поверх него.

Текущая архитектурная граница:

```text
Qt presentation / production composition
                ↓
application use cases + structural ports
                ↓
domain / pipeline services
                ↓
recording, persistence and external infrastructure
```

К текущему состоянию завершены P0-стабилизация и **Wave 2 / Slices 1–12**:

- Qt-free `RecordingWorkflowController`;
- `StartRecordingUseCase` для start transaction и rollback;
- application-owned stop/finalize semantics;
- `RecoverRecordingUseCase` с audio-first recovery;
- физическое удаление legacy start/stop/recovery orchestration из base UI;
- `AudioPreflightUseCase` и удаление legacy preflight callbacks;
- `RecordingRuntimeRecorder`, `RecordingLevelsSnapshot` и `RecordingHealthSnapshot` вместо concrete `DualRecorder` в base UI;
- `RefreshAudioDevicesUseCase` для discovery/resolution/hot-plug;
- stable microphone identity, переживающая PortAudio reindex и предпочитающая WASAPI;
- отсутствие direct `sounddevice` / `soundcard` discovery в base `ui/app.py`;
- Qt-free `RecordingHealthMonitor` и typed health assessment для stream errors, callback timeout, silence и dropped-block policy.
- Qt-free `recording_presentation` model для duration, level normalization, health summary, warning/recovery cues и canonical recording-panel phases.
- explicit production composition: общий bootstrap получает `window_type`, а production adapters больше не меняют `base_app.MainWindow` через module-global rebinding; responsibility-bearing MRO закреплён architecture tests.
- Qt-free `TranscriptionQueueCoordinator` для restore/pump/retry/complete/fail decisions; queue UI получает typed snapshot, а `TranscriptionWorker` вынесен из `ui/app.py` в отдельный Qt transport adapter.
- Qt-free `NormalizationCoordinator` для manual/auto scheduling, lifecycle, cancellation/progress и resume decisions; `normalization_presentation` централизует actions/process/result state, а explicit Yandex consent остаётся в UI adapter.
- Qt-free `LatexMonitorCoordinator` для enable/disable, manual/periodic scan eligibility и single-flight state; `latex_monitor_presentation` централизует no-update/success/failure UI state, а `RemoteLatexService` остаётся infrastructure concern.
- Qt-free `ShutdownCoordinator` для `IDLE/DRAINING/READY`, prompt/immediate-close decisions и drain barriers; `shutdown_app` вставлен в production MRO между publication/cockpit и concurrent layers, сохраняя recording finalize, normalization cancellation, background-task shutdown и persisted transcription queue semantics.

**Wave 2 завершён; Wave 3 / Slices 13–17 завершены.** Текущий приоритет — Release 1.0:
production runtime, безопасный backup, disaster recovery, packaging и физическая проверка записи.
**Wave 3 / Slice 18 перенесён после stable `v1.0.0`.** Детали описаны в [`PLAN.md`](PLAN.md).

## Основные возможности

### Запись и аудио

- одновременная запись микрофона и системного звука;
- Windows WASAPI Loopback через `SoundCard`;
- fallback на подходящий input endpoint, включая Stereo Mix;
- безопасные WAV-чанки и `session.json` во время записи;
- bounded queues и отдельные writer-потоки;
- live levels, queue pressure, dropped blocks, writer latency, silence и callback health;
- reconnect logic для system audio;
- отдельные microphone/system tracks и итоговый `lesson.wav`;
- sync report и audio quality report;
- короткий preflight обоих источников перед занятием;
- отдельное прослушивание тестовой дорожки микрофона и звука ученика;
- автоматическое обнаружение hot-plug/reindex перед preflight и стартом;
- аварийное восстановление пригодных чанков.

### Быстрый урок

- минимальный экран старта;
- выбор ученика, предмета и темы;
- профили запуска;
- readiness check;
- тихий preflight;
- отменяемый countdown;
- одна контекстная кнопка для начала/завершения;
- `F9` для быстрого управления;
- автоматический запуск транскрибации после завершения при соответствующем профиле.

### Транскрибация

- локальный `faster-whisper`;
- последовательная фоновая очередь;
- повторное использование загруженной Whisper-модели;
- raw text, timestamped text и segment JSON;
- раздельная транскрибация teacher/student tracks;
- метки говорящего;
- редактор сегментов;
- черновики и ручное подтверждение итогового текста;
- восстановление очереди после перезапуска.

### LLM-фильтрация учебного содержания

После Whisper можно запустить консервативную фильтрацию:

- локально через Ollama;
- через Yandex AI Studio только после явного разрешения пользователя.

Фильтрация не должна переписывать занятие «по смыслу». Её задача — удалить очевидно неучебные фрагменты, сохранив числа, формулы, ошибки ученика, вопросы, домашнее задание и предметные термины.

Результат всегда проходит ручную проверку перед применением.

Основные команды:

```powershell
ollama pull qwen3:8b
uv run tutor-assistant content-filter-doctor
uv run tutor-assistant filter-transcript <lesson-id>
```

Документация:

- [`docs/educational-content-filter.md`](docs/educational-content-filter.md)
- [`docs/resumable-normalization.md`](docs/resumable-normalization.md)
- [`docs/cloud-privacy.md`](docs/cloud-privacy.md)

Yandex API key не хранится в обычном YAML. Он читается из environment/system credential storage. Для cloud processing действует explicit consent policy.

### CRM, расписание и локальный архив

Приложение включает:

- карточки учеников;
- родителей/представителей и контактные данные;
- локальное шифрование чувствительных CRM-полей через Windows DPAPI;
- недельное расписание;
- разовые и повторяющиеся занятия;
- связь scheduled occurrence с `lesson_id`;
- локальный архив материалов;
- импорт аудио и готовых транскриптов;
- версии подтверждённого транскрипта;
- мягкое удаление и корзину;
- FTS-поиск;
- content doctor, backup и restore.

SQLite является долговечным источником истины для локального архива. `lesson.json` и файлы на диске являются проекциями/артефактами, а не заменой транзакционной базы.

### Публикация и материалы

После подтверждения транскрипта Tutor Assistant может:

1. подготовить transcript-only payload;
2. создать отдельную Git-ветку занятия;
3. отправить изменения в `students-26-27`;
4. создать draft PR через GitHub API;
5. сохранить URL PR в состоянии занятия;
6. отслеживать появление LaTeX в ветке;
7. локально скомпилировать PDF;
8. опубликовать PDF/log/report обратно в ветку.

`gh` CLI **не является обязательной production-зависимостью** для создания draft PR. Он может использоваться как дополнительный диагностический инструмент:

```powershell
gh auth status
```

### LaTeX / PDF

Поддерживаются:

- `latexmk`;
- `pdflatex`, `xelatex`, `lualatex`;
- `-no-shell-escape`;
- блокировка опасных команд и путей;
- timeout с завершением process tree;
- проверка TeX Live;
- extraction ошибок;
- PNG preview через `pdftoppm`;
- автоматический мониторинг опубликованных lesson branches;
- bounded retry для исправления TEX.

## Требования

- Windows 10/11;
- Python 3.12 для production; Python 3.13–3.14 только для compatibility testing;
- `uv`;
- Git;
- FFmpeg в `PATH` — рекомендуется;
- TeX Live + `latexmk` — для PDF workflow;
- Poppler / `pdftoppm` — для PNG preview;
- активный Windows playback endpoint — для WASAPI Loopback;
- Ollama + подходящая модель — только для локальной LLM-фильтрации;
- Yandex Cloud credentials — только при явно выбранной cloud-фильтрации;
- локальный `students-26-27` или корректный путь к нему в конфигурации.

## Установка

```powershell
git clone https://github.com/ArtemLevin/tutor-assistant.git
cd tutor-assistant
uv sync --all-extras
uv run python scripts\bootstrap.py
```

Если доступен GNU Make:

```powershell
make init
```

Основные команды:

```powershell
make help
make run
make setup
make doctor
make check
make build
```

Прямые uv-эквиваленты:

```powershell
uv run --all-extras tutor-assistant-gui config\app.yaml
uv run --all-extras tutor-assistant --config config\app.yaml doctor
uv run --all-extras pytest -q
uv run --all-extras ruff check .
uv build
```

## Конфигурация

Создайте рабочую конфигурацию:

```powershell
Copy-Item config\app.example.yaml config\app.yaml
```

Минимальный repository block:

```yaml
repository:
  students_repo: ../students-26-27
  repository_full_name: ArtemLevin/students-26-27
  pr_base_branch: main
```

Рекомендуемая структура локальных каталогов:

```text
C:\Users\<user>\IdeaProjects\
├── tutor-assistant\
├── students-26-27\
└── .tutor-assistant-worktrees\
```

Не размещайте `students-26-27` внутри Git-репозитория `tutor-assistant`.

Пример audio settings:

```yaml
recording:
  sample_rate: 48000
  channels: 1
  mic_device: null
  mic_device_name: null
  mic_host_api: null
  system_device_id: null
  system_backend: soundcard
  chunk_seconds: 30
  diagnostics_seconds: 5
  queue_blocks: 256
  target_sample_rate: 48000
  dual_channel_transcription: true
  require_preflight: true
  silence_warning_seconds: 20
  device_timeout_seconds: 5
```

Numeric PortAudio index не считается стабильной идентичностью. Приложение сохраняет имя/host API микрофона и при новом discovery пытается разрешить актуальный endpoint, предпочитая одноимённый WASAPI device.

## Запуск GUI

Production запуск:

```powershell
uv run --all-extras tutor-assistant-gui config\app.yaml
```

Повторный запуск setup wizard:

```powershell
uv run --all-extras tutor-assistant-gui --setup config\app.yaml
```

или:

```powershell
make setup
```

Если конфигурация находится в другом месте:

```powershell
uv run --all-extras tutor-assistant-gui C:\path\to\app.yaml
```

Production console entrypoint из `pyproject.toml`:

```text
tutor-assistant-gui = tutor_assistant.ui.recording_recovery_app:main
```

## Работа с аудио

### Выбор устройств

Перед preflight и стартом production adapter выполняет fresh discovery:

```text
list_input_devices
+ list_system_audio_sources
        ↓
RefreshAudioDevicesUseCase
        ↓
stable microphone/system selection
        ↓
UI inventory + optional endpoint probe
```

Обычное отключение/подключение USB-гарнитуры больше не требует штатного restart Tutor Assistant. Если Windows действительно перестала публиковать endpoint, приложение покажет понятную ошибку и попросит переподключить/выбрать устройство.

### Preflight

Кнопка проверки выполняет короткую diagnostic capture через `AudioPreflightUseCase`. UI получает typed result и не разбирает `quality_report.json` самостоятельно.

### Запись

Во время записи создаётся структура вида:

```text
data/lessons/<lesson_id>/recording/
├── session.json
├── chunks/
│   ├── microphone/
│   └── system/
├── microphone.wav
├── system.wav
├── lesson.wav
├── sync_report.json
└── audio_quality_report.json
```

Точное наличие финальных файлов зависит от стадии, формата и успешности finalize.

### Восстановление

Recovery следует принципу **audio first**: сначала предпринимается физическое восстановление пригодного аудио, затем metadata lookup/finalization.

Production GUI предлагает восстановить незавершённые recording sessions при старте.

CLI-вариант низкоуровневого восстановления:

```powershell
tutor-assistant-recover data\lessons\<id>\recording
```

## Типовой рабочий цикл

1. Выберите ученика, предмет и тему.
2. Проверьте выбранные microphone/system endpoints.
3. Выполните preflight.
4. Запустите запись.
5. Завершите занятие — приложение безопасно остановит recorder и выполнит finalize.
6. Запустите или дождитесь фоновой транскрибации.
7. Откройте транскрипт и проверьте числа, формулы, имена и реплики.
8. При необходимости выполните LLM-фильтрацию и вручную примите/отклоните результат.
9. Подтвердите транскрипт.
10. Опубликуйте lesson branch / draft PR.
11. Проверьте generated LaTeX/PDF/web materials.
12. Выполните merge после ручной проверки.

## CLI

Полная диагностика:

```powershell
make doctor
```

Строгая/машиночитаемая диагностика:

```powershell
make doctor-json
make doctor-strict
```

Показать audio endpoints:

```powershell
uv run tutor-assistant devices
```

Content maintenance:

```powershell
uv run tutor-assistant content-index
uv run tutor-assistant content-doctor
uv run tutor-assistant content-doctor --json --strict
uv run tutor-assistant content-doctor --repair --rebuild-search
uv run tutor-assistant content-backup --create
uv run tutor-assistant content-backup
```

LaTeX:

```powershell
uv run tutor-assistant latex-doctor
uv run tutor-assistant compile C:\lessons\lesson.tex
uv run tutor-assistant scan-latex
```

## Проверка проекта

Основная локальная проверка:

```powershell
make check
```

Отдельно:

```powershell
make lock-check
make lint
make format-check
make test
```

CI включает Windows matrix для Python 3.11–3.14, privacy history gate и отдельную accessibility/scaling matrix 100%, 125%, 150%, 200%.

Для существенных recording refactors рабочий стандарт проекта — получать как минимум два независимых успешных full-suite Windows runs перед squash merge.

## Безопасность и privacy

Ключевые инварианты:

- `data/`, WAV и локальная SQLite-база не должны попадать в Git;
- аудио не отправляется в LLM/cloud providers;
- Yandex AI Studio получает только явно разрешённый текстовый payload;
- credentials не хранятся в tracked YAML;
- recovery не должен терять пригодные чанки из-за metadata failure;
- progressed lesson status не должен откатываться назад при recovery;
- destructive content operations координируются через SQLite leases;
- base UI не должен вновь получать concrete audio infrastructure dependencies.

## Актуальные ограничения

- Windows/PortAudio всё ещё может физически потерять endpoint или вернуть backend-level error; application hot-plug resolution уменьшает необходимость restart, но не может исправить драйвер/Windows audio stack;
- коррекция drift рассчитана на ограниченные расхождения; крупные отклонения требуют проверки hardware path;
- сложные формулы и спорные фрагменты транскрипта по-прежнему проверяет преподаватель;
- draft PR требует корректных GitHub credentials и `repository_full_name`; `gh` CLI необязателен;
- визуальная проверка сложной LaTeX/web-вёрстки остаётся ручным этапом;
- ChatGPT Work / scheduled automation обнаруживает задания периодически; прямой event-driven HTTP trigger из Tutor Assistant не реализован.

## План развития

Актуальный план разработки поддерживается отдельно от README:

**[`PLAN.md`](PLAN.md)**

На текущем этапе Wave 2 и Wave 3 / Slices 13–16 завершены. Приоритет Wave 3 — teacher cockpit / parallel-review synchronization через typed workspace context и event-driven refresh.
