# Transcript Normalization

`Transcript Normalization` — необязательный этап после Whisper. Provider получает
блок транскрипта и возвращает обычный текст без JSON и Markdown. Результат
сохраняется отдельно и применяется только после проверки преподавателем.

Поддерживаются:

- локальный Ollama — provider по умолчанию;
- Yandex AI Studio Responses API — явный облачный opt-in.

## Контракт plain text

Модель возвращает только нормализованные целевые реплики:

```text
[П] Сегодня рассмотрим свойства логарифмов.
[У] Я не понимаю, почему основание должно быть положительным.
```

Приложение отклоняет JSON, кодовые блоки, новый текст, новые числа и новые
формульные токены. Потеря числа, части формулы или необычно большой объём
удаления создают предупреждение для ручной проверки.

Промпт `transcript-normalizer.v2` отдельно защищает термины школьной математики:
логарифмы, уравнения и неравенства, функции и графики, производные, интегралы,
прогрессии, тригонометрию, планиметрию, стереометрию, вероятность, статистику,
текстовые задачи, ОГЭ/ЕГЭ, условия и домашнее задание.

## Локальный Ollama

Конфигурация по умолчанию:

```yaml
normalization:
  enabled: true
  provider: ollama
  base_url: http://127.0.0.1:11434
  allow_remote_endpoint: false
  model: qwen3:8b
```

Подготовка Windows:

```powershell
ollama pull qwen3:8b
ollama serve
uv run tutor-assistant normalization-doctor
```

Удалённый Ollama запрещён по умолчанию.

## Yandex AI Studio

Облачный режим отправляет транскрипт в Yandex Cloud. Он включается только
явной конфигурацией:

```yaml
normalization:
  enabled: true
  provider: yandex_ai_studio
  allow_cloud_processing: true
  yandex_base_url: https://ai.api.cloud.yandex.net/v1
  yandex_folder_id: <folder-id>
  yandex_api_key_env: YANDEX_AI_STUDIO_API_KEY
  yandex_model: yandexgpt-lite
```

API-ключ хранится только в переменной окружения:

```powershell
$env:YANDEX_AI_STUDIO_API_KEY = "<API-key>"
uv run tutor-assistant normalization-doctor --provider yandex_ai_studio
```

Сервисному аккаунту требуется роль `ai.languageModels.user`, ключу — scope
`yc.ai.languageModels.execute` или совместимый `yc.ai.foundationModels.execute`.
Ключ, промпт и текст ответа в логи не записываются.

Используется официальный Responses API:

```text
POST https://ai.api.cloud.yandex.net/v1/responses
Authorization: Api-Key <API-key>
model: gpt://<folder-id>/<model>
```

## CLI

```powershell
uv run tutor-assistant normalize <lesson-id>
uv run tutor-assistant normalize <lesson-id> --model qwen3:14b
uv run tutor-assistant normalize <lesson-id> --provider yandex_ai_studio
uv run tutor-assistant normalize <lesson-id> --force
uv run tutor-assistant normalize <lesson-id> --dry-run
uv run tutor-assistant normalize <lesson-id> --output .\result.txt --dry-run
```

Команда печатает нормализованный текст. `--dry-run` создаёт временный или
указанный TXT, не создаёт `normalization_runs`, не меняет занятие и ревизию.

## GUI

На вкладке «Транскрипт» отображается provider из конфигурации.

1. Выберите модель.
2. Нажмите «Нормализовать».
3. Проверьте вкладки «Изменения», «Исходный текст», «Нормализованный текст» и
   «Предупреждения».
4. При необходимости отредактируйте результат.
5. Примените его как новую ревизию или отклоните.

Ollama не запускается одновременно с активной Whisper-транскрибацией на CPU.
Облачный provider не занимает локальный CPU этим ограничением.

## Хранение и восстановление

```text
data/lessons/<lesson_id>/transcript/transcript_normalized.txt
data/lessons/<lesson_id>/transcript/normalization_manifest.json
```

TXT содержит только результат. Manifest содержит provider, модель, prompt
version, SHA-256 источника, configuration hash, статистику и предупреждения.

Таблица `normalization_runs` сохраняет идемпотентный ключ
`lesson_id + source_sha256 + model + prompt_version + configuration_hash`.
Состояние `running` после перезапуска восстанавливается как `pending`.
Несовпадение `source_sha256` блокирует применение.

## Ручной smoke-test

1. Создайте обезличенное занятие и завершите транскрибацию.
2. Сохраните SHA-256 и размер `00_raw_segments.json`.
3. Запустите normalization doctor выбранного provider.
4. Выполните нормализацию и проверьте `transcript_normalized.txt`.
5. Убедитесь, что приветствия удалены, формулы, логарифмы, неравенства, вопрос
   и ошибка ученика, условие и домашнее задание сохранены.
6. Проверьте предупреждения о числах, формулах и высокой доле удаления.
7. Измените исходный сегмент после запуска: применение должно блокироваться.
8. Повторите запуск и примените результат как новую ревизию.
9. Для Ollama остановите сервер во время запроса. Для Yandex временно удалите
   переменную API-ключа. Оба отказа должны оставить занятие доступным.

## Тесты

```powershell
uv run ruff check .
uv run pytest

$env:TUTOR_ASSISTANT_OLLAMA_TEST = "1"
uv run pytest -m ollama

$env:TUTOR_ASSISTANT_YANDEX_TEST = "1"
$env:YANDEX_FOLDER_ID = "<folder-id>"
$env:YANDEX_AI_STUDIO_API_KEY = "<API-key>"
uv run pytest -m yandex
```

Обычный `pytest` не требует Ollama и облачных credentials.
