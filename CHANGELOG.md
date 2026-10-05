# Changelog

Изменения описывают пользовательский и эксплуатационный контракт проекта.

## 1.0.0rc1 — Release 1.0 readiness

- Recording finalize UI снова корректно подготавливает следующий урок после фоновой транскрибации: восстановлен потерянный при refactor reset-helper, а durable завершённая запись больше не оставляет application workflow в `STOPPING` при последующей UI/queue ошибке.
- Automatic publication recovery теперь следует immutable revision, закреплённой в durable job:
  более новая пустая revision не блокирует валидный ожидающий job; startup отдельно карантинирует
  stale `running` empty intents, а repair атомарно проверяет old-empty/new-meaningful revisions и SHA.
- Пустой результат ASR больше не считается успешной транскрибацией: zero-segment и
  semantically empty output переводят задание в ошибку с технической диагностикой, не создают
  automatic revision/publication job и не попадают в Git; короткие содержательные записи не
  ограничиваются искусственным порогом длительности. Legacy unpublished empty intents
  блокируются и могут быть транзакционно заменены только после успешной повторной транскрибации.
- Per-lesson opt-in `AUTO_TRANSCRIPT_GITHUB` теперь доступен как в «Быстром уроке», так и в расширенном экране `01 Занятие`; переключатели синхронизированы и блокируются после старта записи.
- Архив материалов и корзина теперь поддерживают множественный выбор занятий: выбранные
  занятия можно пакетно переместить в корзину, а затем пакетно удалить локальные данные
  навсегда с отображением освобождённого объёма; ошибки отдельных занятий не прерывают
  обработку остальных, а активные запись/транскрибация сохраняют защиту от удаления.
- Добавлен opt-in режим `AUTO_TRANSCRIPT_GITHUB`: после успешного завершения записи занятие
  автоматически проходит persistent ASR queue, создаёт immutable automatic-transcription revision
  в SQLite и публикует transcript через durable publication queue без подмены teacher-approved
  семантики `READY`.
- Automatic publication использует transcript-only egress, immutable revision/SHA/path,
  isolated Git worktree, `--force-with-lease`, remote commit/content verification и fail-closed
  collision policy; transient Git failures имеют bounded retry, а blocked/conflict состояния
  видимы в фоновой очереди и переживают restart.
- Safe shutdown теперь учитывает publication worker как drain barrier; waiting/retry publication
  jobs сохраняются для следующего запуска.
- Добавлен end-to-end regression contract
  `stop → ASR → SQLite revision → publication queue → verified Git publication`
  с реальным локальным bare Git remote.
- Automatic transcript publication поддерживает отдельный `automatic_transcript_repository`:
  public Pages/materials repository остаётся в `repository`, а машинные транскрипты можно
  маршрутизировать в независимый PRIVATE GitHub repository; старые конфигурации сохраняют
  безопасный fallback на `repository`.

- Python 3.12 утверждён production runtime; Python 3.13/3.14 вынесены в compatibility CI.
- Добавлен стабильный aggregate `Release 1.0 Gate` с privacy, architecture,
  accessibility, packaging и production test contracts.
- Каждый запуск получает application session/build identity; unhandled Python/Qt failures
  оставляют allowlisted crash marker без пользовательских текстов и credentials.
- Support bundle v2 включает безопасные build, backup, crash и workspace metadata.
- Реализованы Qt-free automatic backup scheduling, обязательная verification,
  restart-persistent status и retention только для scheduled copies.
- Добавлен полностью isolated disaster-recovery drill для restore, quarantine,
  rollback-to-safety и dual/single-channel audio recovery.
- Добавлены PyInstaller onedir portable, Inno Setup installer, program/user-data separation,
  install/reinstall/uninstall smoke и artifact privacy scan.
- Подготовлена автоматическая публикация Windows assets, SHA-256, immutable build manifest
  и optional verified code signing с явной unsigned exception policy.
- Добавлены privacy-safe hardware soak collector и обязательные physical acceptance thresholds.
- Добавлена эксплуатационная документация по установке, повседневной работе,
  восстановлению, поддержке, hardware soak и release governance.
- Исправлена семантика расписания: завершение повторяющейся серии сохраняет историю,
  корректно обрабатывает уже материализованные будущие даты и не допускает неявного
  восстановления серии; для ошибочно созданного разового занятия добавлено явное удаление.
- Отменённые и удалённые из активного расписания занятия больше не занимают календарные
  ячейки после обновления; cancellation history и связанные метаданные при этом сохраняются.
- Расписание уплотнено до часовой сетки 09:00–20:00: в календарной ячейке отображается только
  имя ученика, а оплата, получение ДЗ и остальные параметры доступны в карточке занятия;
  старые записи на половину часа сохраняются без миграции и отображаются в часовом блоке.
- Освободившийся после отмены слот снова можно занять новым разовым занятием: создание больше
  не открывает скрытую отменённую запись, а её восстановление вынесено в отдельное действие.
- Усилена целостность расписания: перенос occurrence между неделями больше не создаёт
  виртуальный дубликат исходной даты, а конфликтующие create/update/restore операции
  сериализуются одной SQLite write transaction.
- Изменение активной повторяющейся серии теперь действует с выбранной даты вперёд: прошлые
  virtual lessons сохраняют исходные параметры, а materialized payment/homework/lesson metadata
  остаются привязаны к тем же occurrence ids.
- Статус `recording_failed` включён в schedule lifecycle и защищён от отмены/удаления как
  будущего занятия; неактивные legacy-series больше нельзя восстановить через store-level API.
- Новые и изменяемые занятия не могут выходить за рабочую сетку 09:00–21:00 по времени
  окончания; сохранённые legacy-записи вне сетки не удаляются и явно отражаются в статистике.
- При открытии существующей БД legacy-удалённые серии (`active=0` без `ended_from`)
  больше не оставляют скрытые `planned` occurrences, блокирующие повторное использование слота:
  безопасные несвязанные записи нормализуются в cancellation tombstones с сохранением metadata.

## 0.22.1 — Production safety hardening

- Завершены Wave 2 recording composition и Wave 3 orchestration slices 13–17.
- Усилены authoritative transcript publication, audio-first recording recovery,
  transactional restore, lease safety и synchronization recording/review contexts.
