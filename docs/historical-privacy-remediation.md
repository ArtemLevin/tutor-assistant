# Historical privacy remediation and policy gate

## Назначение

Этот контур закрывает риск сохранения runtime-конфигурации, персональных данных и локальных артефактов в Git history Tutor Assistant.

Политика хранится в [`policy/privacy-history.json`](../policy/privacy-history.json). Она задаёт:

- commit, после которого новые изменения проходят обязательный history gate;
- пути, запрещённые в новых commits и текущем дереве;
- пути, подлежащие удалению из прежней истории;
- обязательную visibility `PRIVATE` для полного аудита и rewrite.

Текущий набор запрещённых путей:

```text
.env
config/app.yaml
config/students.yaml
data/
```

Файлы-примеры `config/*.example.yaml` разрешены.

## Два режима аудита

### `head`

Используется на каждом pull request и push в `main`.

```powershell
python scripts/history_privacy.py audit `
  --mode head `
  --report privacy-history-report.json
```

Проверяются:

1. текущее дерево `HEAD`;
2. все Git objects в диапазоне `baseline_commit..HEAD`;
3. наличие baseline и его принадлежность ancestry текущего `HEAD`.

Проверка objects обнаруживает сценарий, где запрещённый файл добавили в одном commit и удалили в следующем.

### `full`

Используется после локального rewrite и для post-clone verification.

```powershell
python scripts/history_privacy.py audit `
  --mode full `
  --require-visibility `
  --report privacy-history-report.json
```

Проверяются все reachable branches, tags и текущая visibility GitHub-репозитория. Успешный результат требует `PRIVATE`.

## CI

Workflow `Privacy history gate` запускает `head`-проверку для каждого PR и push в `main`.

Ручной запуск workflow с параметром `mode=full` предназначен для финальной проверки после rewrite. До перевода репозитория в `PRIVATE` полный gate закономерно завершается ошибкой `invalid_visibility`.

## Предварительные условия rewrite

1. Остановить merges и публикацию commits.
2. Убедиться, что все collaborators уведомлены.
3. Сохранить локальные незакоммиченные изменения отдельно.
4. Перевести GitHub-репозиторий в `PRIVATE`:

```powershell
gh repo edit ArtemLevin/tutor-assistant `
  --visibility private `
  --accept-visibility-change-consequences
```

5. Установить `git-filter-repo` и проверить доступность:

```powershell
git filter-repo --version
```

6. Подготовить пустой каталог вне рабочего clone, например:

```powershell
New-Item -ItemType Directory C:\privacy-remediation\tutor-assistant
```

## Просмотр точного rewrite-плана

```powershell
python scripts/history_privacy.py plan
```

Команда выводит `git filter-repo --invert-paths` с путями из policy.

## Локальный rewrite с обязательным backup

Этот запуск создаёт проверенный mirror и backup, оставляя remote без изменений:

```powershell
python scripts/history_privacy.py rewrite `
  --repository-url https://github.com/ArtemLevin/tutor-assistant.git `
  --output-dir C:\privacy-remediation\tutor-assistant `
  --execute
```

Перед rewrite создаются:

- `pre-rewrite-backup.bundle` — полный backup всех refs;
- `pre-rewrite-refs.json` — SHA branches и tags;
- `COLLABORATOR_NOTICE.md` — готовое уведомление для владельцев clone;
- `rewritten-mirror.git` — локально очищенный mirror;
- `rewritten-history-audit.json` — full audit очищенного mirror;
- `post-rewrite-privacy-history.json` — policy с новым baseline SHA.

Локальный rewrite завершается ошибкой при любом найденном запрещённом пути.

## Force-push

Force-push требует одновременно:

- `--execute`;
- `--force-push`;
- точную фразу `REWRITE_TUTOR_ASSISTANT_HISTORY`;
- GitHub visibility `PRIVATE`;
- успешное создание и проверку backup bundle;
- успешный full audit локального rewritten mirror.

```powershell
python scripts/history_privacy.py rewrite `
  --repository-url https://github.com/ArtemLevin/tutor-assistant.git `
  --output-dir C:\privacy-remediation\tutor-assistant `
  --execute `
  --force-push `
  --confirm REWRITE_TUTOR_ASSISTANT_HISTORY
```

Для каждого branch/tag применяется отдельный `--force-with-lease` с pre-rewrite SHA. Изменившийся remote ref блокирует отправку и сохраняет возможность повторной оценки ситуации.

После push инструмент создаёт новый mirror clone и выполняет full audit с проверкой PRIVATE visibility. Отчёт сохраняется как `post-rewrite-clone-audit.json`.

## Действия после успешного rewrite

1. Сохранить каталог remediation и bundle в защищённом месте.
2. Запустить workflow `Privacy history gate` с `mode=full`.
3. Проверить JSON artifact: `passed=true`, `findings=[]`, `visibility=PRIVATE`.
4. Уведомить collaborators содержимым `COLLABORATOR_NOTICE.md`.
5. Архивировать прежние clone и выполнить свежий clone.
6. Запретить push локальных branches, основанных на прежних SHA.
7. Проверить forks, cached pull-request refs и обращения к GitHub Support при необходимости окончательной server-side очистки.
8. Обновить локальный `policy/privacy-history.json` содержимым `post-rewrite-privacy-history.json`, если rewrite выполнялся из версии инструмента вне очищаемого mirror.

## Восстановление из backup

Проверка bundle:

```powershell
git bundle verify C:\privacy-remediation\tutor-assistant\pre-rewrite-backup.bundle
```

Восстановительный mirror:

```powershell
git clone --mirror `
  C:\privacy-remediation\tutor-assistant\pre-rewrite-backup.bundle `
  C:\privacy-remediation\restore.git
```

Возврат прежних refs является отдельной destructive операцией. Перед ним требуется повторная оценка privacy-риска и точное сопоставление с `pre-rewrite-refs.json`.

## Границы автоматизации

Обычный PR может добавить policy, tests, CI и инструменты подготовки. Исторический force-push меняет SHA всех затронутых commits и выполняется отдельной эксплуатационной процедурой владельца репозитория.

Инструмент хранит backup до rewrite, использует PRIVATE gate, typed confirmation, force-with-lease и post-clone verification. Эти условия предназначены для предотвращения случайного или устаревшего history rewrite.
