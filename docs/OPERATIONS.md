# Ежедневная эксплуатация

## Перед уроком

1. Запустите Tutor Assistant и убедитесь, что выбран правильный рабочий каталог.
2. Проверьте состояние приложения: `tutor-assistant --config config\app.yaml doctor`.
3. Убедитесь, что `Production runtime` соответствует Python 3.12 для packaged build.
4. Проверьте `Automatic backup`: последняя копия должна быть `verified`, а поле ошибки пустым.
5. Выберите ученика, предмет и тему; дождитесь успешной проверки микрофона и системного звука.
6. Если нужен полностью автоматический post-lesson flow, до старта записи включите «Автоматически транскрибировать и отправить на GitHub». Режим относится только к создаваемому занятию и не становится глобальным default.
7. Для automatic mode рекомендуется отдельный PRIVATE checkout в `automatic_transcript_repository`. Public GitHub Pages repository остаётся в `repository`; automatic publisher его не использует, если dedicated target настроен.

## Во время урока

- Начало/завершение записи выполняется основной кнопкой либо клавишей `F9`.
- Контекст активной записи независим от контекста просмотра другого ученика.
- При предупреждении об отсутствии сигнала проверьте устройство, не удаляя WAV-чанки.
- Backup не запускается во время критических recording transitions, restore или shutdown drain.

## После урока

Дождитесь завершения записи и появления канонического `lesson.wav`.

В manual mode запустите локальную транскрибацию, проверьте полученный текст, явно подтвердите
revision и затем выполните публикацию.

В automatic mode после успешного stop приложение само выполняет:

```text
lesson.wav
→ persistent transcription queue
→ local ASR
→ immutable automatic-transcription revision
→ persistent publication queue
→ verified GitHub publication
→ PUBLISHED
```

Целевой путь строится из даты занятия:

```text
<student.repository_folder>/transcript/DD.MM.YY.txt
```

В GitHub отправляется только ожидаемый текстовый файл. Existing target с другим содержимым
не перезаписывается и помечается как conflict. Временные Git-ошибки автоматически повторяются
с bounded backoff; blocked publication требует исправить причину и явно выбрать повтор,
а conflict разрешается вручную.

Обычное закрытие приложения сохраняет незавершённые transcription/publication jobs.
При следующем запуске automatic publication intents reconciled с SQLite и durable queue
продолжает обработку без повторного создания automatic revision.

## Автоматические резервные копии

Настройки в конфигурации:

```yaml
content:
  backup_enabled: true
  backup_interval_hours: 24
  backup_retention_count: 14
```

Каждый цикл выполняет `create → verify → prune scheduled only`. Копии `manual`,
`pre-restore-safety` и `pre-upgrade` не удаляются плановой retention-политикой.
Статус хранится в `<workspace>\maintenance\backup-status.json`; ошибка остаётся
видимой до следующего успешного цикла. При failed verify существующие копии не очищаются.

## Журналы и завершение

Журнал приложения находится в `<workspace>\logs\application.log`; каждая запись содержит
идентификатор application session. Закрывайте приложение через штатный интерфейс: shutdown
останавливает запуск нового backup и ожидает уже начатые safe background operations,
включая активные transcription/publication workers. Ожидающие durable jobs не теряются.
При следующем старте после crash приложение предлагает собрать диагностику или открыть журнал.

## Команды

```powershell
tutor-assistant --config config\app.yaml doctor
tutor-assistant --config config\app.yaml content-backup
tutor-assistant --config config\app.yaml content-doctor --json
tutor-assistant --config config\app.yaml support-bundle
tutor-assistant --config config\app.yaml recovery-drill
```


## Диагностика automatic publication

Состояния publication job видны в разделе фоновой обработки:

- `waiting` / `running` — нормальная обработка;
- `retry_required` — временная Git-ошибка, следующий retry назначен автоматически;
- `blocked` — configuration/security/runtime prerequisite требует исправления и явного retry;
- `conflict` — удалённый target уже содержит другой transcript; автоматическая перезапись запрещена;
- `published` — remote commit/content подтверждены.

При `blocked` сначала устраните причину (repository configuration, доступ, policy), затем откройте
job и выберите повтор. `conflict` не переводится в retry автоматически.

Подробности: [`AUTOMATIC_TRANSCRIPT_PUBLICATION.md`](AUTOMATIC_TRANSCRIPT_PUBLICATION.md).
