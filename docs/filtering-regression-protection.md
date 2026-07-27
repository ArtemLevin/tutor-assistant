# Защита LLM-фильтрации от регрессий

Документ описывает эксплуатационные гарантии, добавленные после инцидента с PR 33,
который был объединён из устаревшей ветки и частично отменил функциональность PR 31–32.

## Защищаемые контракты

CI рассматривает следующие свойства как публичные контракты приложения:

- пакет `tutor_assistant.normalization` импортируется без побочных ошибок;
- совместимые aliases `EducationalContentFilterService` и `FilteredTranscript`
  объявлены ровно один раз;
- доступны профили `mathematics`, `physics`, `chemistry` и `generic`;
- версия промпта определяется выбранным предметным профилем;
- Ollama и Yandex AI Studio получают предметный системный prompt;
- provider doctor создаёт запрос с явными `lesson_subject` и `subject_profile`;
- GUI не перекрывает функцию `provider_label` локальной переменной;
- выбранные модели Ollama и Yandex сохраняются независимо;
- облачный API-ключ не попадает в request payload, manifest и журнал.

## Порядок CI

Windows workflow выполняет проверки на Python 3.11, 3.12, 3.13 и 3.14:

1. проверка `uv.lock`;
2. установка desktop/dev-зависимостей;
3. Ruff;
4. `compileall`;
5. быстрый import smoke фильтрации;
6. focused suite контрактов фильтрации;
7. `git diff --check`;
8. полный pytest;
9. публикация JUnit-отчёта.

Python 3.14 больше не является необязательной средой: сбой любой версии делает job
неуспешным.

## CODEOWNERS

Изменения в следующих областях требуют владельца репозитория:

- `src/tutor_assistant/normalization/`;
- `src/tutor_assistant/ui/app.py`;
- `src/tutor_assistant/ui/normalization_provider.py`;
- `.github/workflows/`;
- основной набор контрактов фильтрации.

## Рекомендуемые настройки `main`

В настройках GitHub следует включить ruleset или branch protection со следующими
условиями:

- изменения только через pull request;
- обязательный успешный check `Windows student content`;
- обязательное обновление ветки относительно `main` перед merge;
- запрет merge draft PR;
- запрет прямого push и force-push;
- сброс approvals после нового commit;
- удаление head-ветки после merge.

Настройки репозитория не хранятся в Git и должны проверяться отдельно от кода.
