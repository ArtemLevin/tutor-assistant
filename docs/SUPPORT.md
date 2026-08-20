# Диагностика и поддержка

## Идентификация запуска

Каждый запуск получает уникальный `application_session_id`. Журнал и build metadata
содержат версию приложения, commit SHA, release channel, Python version, платформу,
архитектуру и режим `frozen/source`.

Основной журнал: `<workspace>\logs\application.log`. Журналы ротируются и проходят
редактирование API keys, Bearer credentials и других известных секретов.

## Crash marker

Unhandled исключения main thread, background thread и fatal Qt messages сохраняют:

```json
{
  "timestamp": "2026-08-20T12:00:00+00:00",
  "version": "1.0.0rc1",
  "session_id": "application-session-id",
  "exception_type": "RuntimeError",
  "component": "main-thread",
  "recording_active": false,
  "transcription_active": false
}
```

Путь: `<workspace>\crash\last-crash.json`. Exception message, имена учеников,
транскрипты, audio bytes, credentials и cloud payload не входят в allowlist.
При следующем запуске приложение неблокирующе предлагает открыть журнал или собрать диагностику.

## Support bundle v2

```powershell
tutor-assistant --config config\app.yaml support-bundle --output ..\support.zip
```

В безопасный ZIP входят:

- `manifest.json`, `environment.json`, `devices.json`;
- sanitized configuration и redacted application logs;
- `build-info.json`, `workspace-health.json`, `backup-status.json`;
- `crash/last-crash.json`, если доступен valid allowlisted marker;
- ограниченный набор безопасных recording session/sync/quality manifests.

Фактические `.wav`, `.mp3`, содержимое транскриптов, raw cloud payload и ключи
не включаются. Malformed log bytes не блокируют сборку bundle. Автоматической
отправки telemetry нет: пользователь сам решает, кому передавать архив.

Перед отправкой откройте ZIP и при необходимости дополнительно проверьте его содержимое.
