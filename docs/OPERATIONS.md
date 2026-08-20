# Ежедневная эксплуатация

## Перед уроком

1. Запустите Tutor Assistant и убедитесь, что выбран правильный рабочий каталог.
2. Проверьте состояние приложения: `tutor-assistant --config config\app.yaml doctor`.
3. Убедитесь, что `Production runtime` соответствует Python 3.12 для packaged build.
4. Проверьте `Automatic backup`: последняя копия должна быть `verified`, а поле ошибки пустым.
5. Выберите ученика, предмет и тему; дождитесь успешной проверки микрофона и системного звука.

## Во время урока

- Начало/завершение записи выполняется основной кнопкой либо клавишей `F9`.
- Контекст активной записи независим от контекста просмотра другого ученика.
- При предупреждении об отсутствии сигнала проверьте устройство, не удаляя WAV-чанки.
- Backup не запускается во время критических recording transitions, restore или shutdown drain.

## После урока

Дождитесь завершения записи и появления канонического `lesson.wav`. Запустите локальную
транскрибацию, проверьте полученный текст и явно подтвердите revision перед публикацией.
Обычное закрытие приложения сохраняет очередь незавершённой транскрибации для следующего запуска.

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
останавливает запуск нового backup и ожидает уже начатые safe background operations.
При следующем старте после crash приложение предлагает собрать диагностику или открыть журнал.

## Команды

```powershell
tutor-assistant --config config\app.yaml doctor
tutor-assistant --config config\app.yaml content-backup
tutor-assistant --config config\app.yaml content-doctor --json
tutor-assistant --config config\app.yaml support-bundle
tutor-assistant --config config\app.yaml recovery-drill
```
