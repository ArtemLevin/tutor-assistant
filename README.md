# Tutor Assistant

Десктоп-сервис для полного цикла подготовки материалов после занятия:

```text
запись микрофона и системного звука
→ локальная транскрибация faster-whisper
→ ручная проверка текста
→ публикация задания в students-26-27
→ генерация LaTeX, плаката и web-эквивалента через ChatGPT Work
```

Проект рассчитан на Windows, PowerShell и локальную обработку аудио. Аудиозаписи остаются на компьютере преподавателя. В репозиторий учеников передаются подтверждённый транскрипт и метаданные задания.

## Возможности MVP

- выбор ученика, предмета, темы и даты;
- выбор микрофона и входа системного звука/loopback;
- одновременная запись двух источников;
- сведение каналов через FFmpeg или локальный fallback;
- локальная транскрибация через `faster-whisper`;
- сырой текст, текст с таймкодами, сегменты JSON и очищенная версия;
- выделение реплик, указывающих на затруднения ученика;
- обязательное подтверждение транскрипта;
- SQLite-история и состояния заданий;
- создание `lesson.json` по формальной схеме;
- копирование задания в релевантную папку `students-26-27`;
- отдельная Git-ветка на каждое занятие;
- готовый prompt для запланированной задачи ChatGPT Work.

## Требования

- Windows 10/11;
- Python 3.11–3.14;
- Git;
- FFmpeg в `PATH` — рекомендуется;
- loopback-устройство Windows, доступное как вход;
- локальная копия `students-26-27` рядом с проектом или путь к ней в конфигурации.

## Установка

```powershell
git clone https://github.com/ArtemLevin/tutor-assistant.git
cd tutor-assistant
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[all]"
```

Для разработки:

```powershell
pip install -e ".[all,dev]"
```

Проверьте FFmpeg:

```powershell
ffmpeg -version
ffprobe -version
```

## Конфигурация

Создайте рабочую конфигурацию:

```powershell
Copy-Item config\app.example.yaml config\app.yaml
```

Ключевой параметр:

```yaml
repository:
  students_repo: ../students-26-27
```

Путь может быть абсолютным:

```yaml
repository:
  students_repo: C:/Users/Артем/IdeaProjects/students-26-27
```

Список учеников хранится в `config/students.yaml`. `id` должен совпадать с именем каталога внутри `students/`.

## Запуск GUI

```powershell
tutor-assistant-gui
```

или:

```powershell
python -m tutor_assistant.ui.app
```

Если конфигурация лежит в другом месте:

```powershell
python -m tutor_assistant.ui.app C:\path\to\app.yaml
```

## Работа с приложением

### 1. Подготовка занятия

Выберите:

- ученика;
- предмет;
- тему;
- дату;
- микрофон;
- вход системного звука.

На вашем компьютере микрофон Fifine и G733 loopback можно выбрать по именам, отображаемым непосредственно в списках приложения.

### 2. Запись

Нажмите «Начать запись». Приложение создаст:

```text
data/lessons/<lesson_id>/recording/
├── microphone.wav
├── system.wav
└── lesson.wav
```

Микрофон и системный звук сохраняются раздельно. После завершения создаётся сведённый `lesson.wav`.

Также можно выбрать уже готовый аудиофайл и перейти сразу к транскрибации.

### 3. Транскрибация

Кнопка «Запустить локальную транскрибацию» загружает модель `small` и создаёт:

```text
transcript/
├── 00_raw_whisper.txt
├── 00_raw_timestamped.txt
├── 00_raw_segments.json
├── 03_content_only_medium.txt
├── transcript_verified.txt
├── important_student_signals.json
└── manifest.json
```

Первый запуск модели может занять дополнительное время.

### 4. Проверка

Откройте вкладку «Транскрипт», исправьте числа, имена и формулы, затем нажмите «Подтвердить транскрипт».

### 5. Публикация

Приложение:

1. обновляет локальную ветку `main` репозитория учеников;
2. создаёт ветку вида `lesson/<student>-<date>-<id>`;
3. формирует каталог занятия;
4. создаёт commit;
5. отправляет ветку в GitHub.

В `students-26-27` появится:

```text
students/<student>/lessons/<date_topic>/
├── lesson.json
├── job.status.json
└── source/
    ├── transcript.txt
    ├── transcript_generated.txt
    ├── transcript_timestamped.txt
    ├── segments.json
    ├── important_student_signals.json
    └── transcription_manifest.json
```

## ChatGPT Work

Готовый текст задачи расположен в:

```text
automation/chatgpt-work-prompt.md
```

Рекомендуемая настройка:

- проект: локальная копия `students-26-27` либо подключённый GitHub;
- режим: отдельный worktree;
- период проверки: каждые 5–15 минут;
- доступ: GitHub plugin и image generation;
- источник правил: prompt из `automation/chatgpt-work-prompt.md`;
- результат: отдельная ветка или PR для проверки.

Задача ищет `job.status.json` со статусом `ready_for_generation`, выполняет три промпта из `pipline/prompts`, создаёт пособие, плакат и HTML, обновляет `index.html`, затем выставляет статус `completed`.

## CLI

Показать аудиоустройства:

```powershell
tutor-assistant devices
```

Создать занятие:

```powershell
tutor-assistant create `
  --student nikol_sarkisyants `
  --subject mathematics `
  --topic "Логарифмические неравенства"
```

Транскрибировать готовое аудио:

```powershell
tutor-assistant transcribe data\lessons\<id>\lesson.json C:\lessons\lesson.wav
```

Опубликовать подтверждённое занятие:

```powershell
tutor-assistant publish data\lessons\<id>\lesson.json
```

## Состояния задания

```text
draft
→ recording
→ recorded
→ transcribing
→ review_required
→ ready_for_generation
→ published
→ generating
→ completed
```

При исключении используется `failed`, а причина сохраняется в `lesson.json`.

## Проверка проекта

```powershell
python -m compileall src tests scripts
pytest
ruff check .
```

Проверить конкретный `lesson.json`:

```powershell
python scripts\check_job.py path\to\lesson.json
```

## Ограничения первой версии

- loopback должен быть доступен Windows как входное устройство;
- запись использует одну частоту дискретизации для обоих источников;
- спорные формулы проверяются преподавателем в текстовом редакторе;
- открытие PR пока выполняется вручную после отправки ветки;
- немедленный внешний запуск конкретного чата ChatGPT Work через HTTP не используется; задача обнаруживается запланированной проверкой.

## Безопасность данных

- `data/`, аудио и локальная SQLite-база исключены из Git;
- в ученический репозиторий отправляется подтверждённый текст;
- перед публикацией следует удалить случайно распознанные персональные или технические фрагменты;
- токены GitHub и другие секреты не следует хранить в YAML-файлах проекта.

