# Восстановление после аварии

SQLite остаётся authoritative source of truth; `lesson.json`, транскрипты и остальные
файлы являются восстанавливаемыми projections. Не удаляйте workspace, backup-каталог
или доступные WAV-чанки при первом признаке сбоя.

## Проверка резервной копии

```powershell
tutor-assistant --config config\app.yaml content-backup
tutor-assistant --config config\app.yaml content-backup --verify PATH.sqlite3
```

Проверяются SHA-256, размер, связанный manifest, SQLite `quick_check` и `foreign_key_check`.
Копия с повреждённым manifest, несовпадающим hash или нечитаемой SQLite не применяется.

## Online restore

```powershell
tutor-assistant --config config\app.yaml content-backup --restore PATH.sqlite3 --yes
```

Перед заменой базы создаётся отдельная `pre-restore-safety` копия. После восстановления
приложение повторно проецирует managed files из SQLite. Если projection завершается ошибкой,
выполняется rollback и восстанавливаются прежняя база и файловые проекции.

## Карантин занятий после backup

Каталоги занятий, созданных позже восстанавливаемой копии, не удаляются и не остаются
в namespace активного архива. Они переносятся в:

```text
<workspace>\.restore-quarantine\<id>\
├── manifest.json
└── lessons\<lesson-id>\
```

Перед ручным возвратом такого занятия сохраните quarantine и обратитесь к ответственному
за поддержку. Не перемещайте каталог обратно во время active restore.

## Offline restore повреждённой SQLite

Та же CLI-команда автоматически переходит к offline restore, если live SQLite не открывается.
Исходная повреждённая база и sidecars `-wal`/`-shm` сохраняются в отдельном
`pre-restore-raw-*` каталоге. Перед повторным запуском убедитесь, что другие процессы
Tutor Assistant не удерживают workspace lease.

## Восстановление записи

При следующем запуске согласитесь восстановить запись либо выполните:

```powershell
tutor-assistant-recover PATH\TO\RECORDING
```

Поддерживаются две дорожки, только microphone и только system audio. Если пригодных чанков
не осталось ни в одной дорожке, приложение сообщает о невосстановимой записи явно.
Повреждённый `session.json` не препятствует восстановлению пригодного аудио.

## Recovery drill

```powershell
tutor-assistant --config config\app.yaml recovery-drill --output ..\recovery-drill.json
```

Проверка создаёт исключительно синтетический temporary workspace, сверяет fingerprint
реального каталога до и после работы и запрещает записывать report внутрь live workspace.
Отчёт включает backup/verify, restore, quarantine, rollback, damaged manifest/SHA и
dual/microphone-only/system-only audio recovery.

## Эскалация

Если rollback также завершился ошибкой, прекратите запись в каталог, сохраните его вместе
с backups/quarantine/raw safety copies и создайте privacy-safe support bundle.
