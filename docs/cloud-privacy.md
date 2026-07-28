# Граница облачной обработки и credentials

Версия 0.15.0 вводит явную privacy boundary для Yandex AI Studio.

## Основные гарантии

- реальный транскрипт не отправляется без consent receipt;
- CLI требует `--allow-cloud`;
- согласие привязано к SHA-256 источника, модели, промпту и chunking-конфигурации;
- повтор неопределённого запроса дополнительно требует `--retry-indeterminate`;
- API-ключ хранится в переменной окружения или Windows Credential Manager;
- ключ не записывается в YAML, SQLite, manifest, checkpoints и support bundle;
- в облачный envelope не входят lesson ID, ФИО, локальные пути и таймкоды;
- облачный аудит содержит только fingerprints, счётчики и статусы.

## Credentials

Совместимый вариант через PowerShell:

```powershell
$env:YANDEX_AI_STUDIO_API_KEY = "<api-key>"
```

Безопасное системное хранилище:

```powershell
uv run tutor-assistant credentials yandex set
uv run tutor-assistant credentials yandex status
uv run tutor-assistant credentials yandex delete
```

Команда `set` использует скрытый ввод и не принимает ключ аргументом командной строки.

## Политика согласия

```yaml
normalization:
  allow_cloud_processing: true
  cloud_policy: ask_every_time
  credential_source: auto
```

Значения `cloud_policy`:

- `disabled` — облачные запросы запрещены;
- `ask_every_time` — каждый логический запуск требует нового согласия;
- `allow_for_session` — точный fingerprint можно повторно использовать до закрытия приложения.

Изменение источника, модели, prompt version или chunking-конфигурации делает старое согласие недействительным.

## CLI

```powershell
uv run tutor-assistant filter-transcript <lesson-id> `
  --provider yandex_ai_studio `
  --allow-cloud
```

Повтор неопределённого запроса:

```powershell
uv run tutor-assistant filter-transcript <lesson-id> `
  --provider yandex_ai_studio `
  --allow-cloud `
  --retry-indeterminate
```

## Privacy Doctor

```powershell
uv run tutor-assistant privacy-doctor
uv run tutor-assistant privacy-doctor --json --strict
```

Проверяются endpoint, cloud policy, источник credentials, отсутствие секретов в YAML,
redaction логов и наличие migration 10.

## Аудит

Migration 10 создаёт:

- `cloud_processing_consents`;
- `cloud_request_events`.

Таблицы не содержат текст занятия, prompt, ответ модели, ФИО или credentials.
