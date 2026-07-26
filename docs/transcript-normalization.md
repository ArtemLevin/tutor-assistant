# Transcript Normalization

`Transcript Normalization` — необязательный локальный этап после Whisper. Он
классифицирует исходные сегменты через Ollama, создаёт отдельный проверяемый JSON
и никогда не заменяет подтверждённый транскрипт без действия преподавателя.

## Границы безопасности

- По умолчанию разрешён только `http://127.0.0.1:11434`.
- Внешние LLM API не используются.
- Запросы к Ollama выполняются последовательно и с `temperature=0`.
- Текст транскрипта, промпт и ответ модели не записываются в журнал.
- `keep` всегда берёт исходный текст; временные границы и роли не приходят от LLM.
- Новый номер или новый формульный токен в `trim` блокирует блок целиком.
- Потеря номера или формульного токена требует ручного внимания.
- Результат получает статус `review_required`; применение создаёт append-only
  ревизию с `created_by=ollama:<model>`.
- Несовпадение `source_sha256` блокирует применение.

## Подготовка Ollama на Windows

```powershell
ollama pull qwen3:8b
ollama serve
uv sync --all-extras
```

Проверка без реального транскрипта:

```powershell
uv run tutor-assistant normalization-doctor
uv run tutor-assistant normalization-doctor --model qwen3:14b --json
```

Doctor использует короткий синтетический фрагмент, проверяет endpoint, модель,
JSON Schema и Pydantic-валидацию structured output.

## CLI

```powershell
uv run tutor-assistant normalize <lesson-id>
uv run tutor-assistant normalize <lesson-id> --model qwen3:14b
uv run tutor-assistant normalize <lesson-id> --force
uv run tutor-assistant normalize <lesson-id> --dry-run
uv run tutor-assistant normalize <lesson-id> --output .\result.json --dry-run
uv run tutor-assistant normalize <lesson-id> --include-removed-text
```

CLI не применяет результат автоматически. `--dry-run` создаёт JSON во временной
или указанной директории, но не создаёт `normalization_runs`, не меняет занятие и
не обновляет ревизию.

## GUI

На вкладке «Транскрипт»:

1. Выберите `qwen3:8b` или `qwen3:14b`.
2. Нажмите «Нормализовать локально».
3. Откройте результат после завершения.
4. Проверьте вкладки «Сравнение», «Исходный текст», «Нормализованный текст»,
   «Удалённые фрагменты» и «Предупреждения».
5. При необходимости исправьте нормализованный текст.
6. Примените его как новую ревизию либо закройте/отклоните.

Открытие результата фонового занятия не переключает активное занятие. Во время
активной Whisper-транскрибации нормализация не запускается, поскольку оба
процесса используют CPU.

## Хранение

Основные файлы:

```text
data/lessons/<lesson_id>/transcript/transcript_normalized.json
data/lessons/<lesson_id>/transcript/normalization_manifest.json
```

Таблица `normalization_runs` хранит идемпотентный ключ
`lesson_id + source_sha256 + model + prompt_version + configuration_hash`.
`running` после перезапуска восстанавливается как `pending`. Повтор с
неизменными параметрами возвращает готовый результат; `--force` переводит
предыдущий логический запуск в `stale`.

## Ручной smoke-test

1. Создайте обезличенное тестовое занятие и завершите Whisper-транскрибацию.
2. Скопируйте SHA-256 и размер `00_raw_segments.json`.
3. Запустите нормализацию `qwen3:8b`.
4. Убедитесь, что исходный файл и `transcript_verified.txt` не изменились.
5. Откройте сравнение: приветствие должно быть `drop`, учебные формулы,
   вопросы и ошибки ученика — сохранены.
6. Измените одну цифру в сегменте после запуска и проверьте, что применение
   блокируется сообщением об изменившемся исходном транскрипте.
7. Верните исходный текст, повторите нормализацию и примените результат.
8. Проверьте новую строку `transcript_revisions`, `created_by`, статус
   `approved`, возможность отката и stale-флаги производных материалов.
9. Остановите Ollama во время тестового запуска: занятие должно остаться
   доступным для обычной ручной проверки.
10. Перезапустите приложение с искусственным `normalization_runs.status=running`
    и убедитесь, что run восстановлен как `pending`.

## Тесты

```powershell
uv run ruff check .
uv run pytest
$env:TUTOR_ASSISTANT_OLLAMA_TEST = "1"
$env:TUTOR_ASSISTANT_OLLAMA_MODEL = "qwen3:8b"
uv run pytest -m ollama
```

Обычный `pytest` не требует Ollama. Интеграционная группа использует только
локальный endpoint и синтетический русский текст.
