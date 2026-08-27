# Changelog

Изменения описывают пользовательский и эксплуатационный контракт проекта.

## 1.0.0rc1 — Release 1.0 readiness

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

## 0.22.1 — Production safety hardening

- Завершены Wave 2 recording composition и Wave 3 orchestration slices 13–17.
- Усилены authoritative transcript publication, audio-first recording recovery,
  transactional restore, lease safety и synchronization recording/review contexts.
