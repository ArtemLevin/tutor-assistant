# Установка Tutor Assistant

## Поддерживаемое окружение

Основная платформа — Windows 10/11 x64. Portable и installer-сборки включают production
Python 3.12, Qt и аудиозависимости: отдельная установка Python или `uv` не требуется.
При запуске из исходников официально поддерживаются Python 3.12–3.14, но production
сборка и обязательный CI gate выполняются только на Python 3.12.

## Installer

1. Скачайте `TutorAssistant-<version>-win64-setup.exe` и `SHA256SUMS.txt` из GitHub Release.
2. Сравните контрольную сумму: `Get-FileHash .\TutorAssistant-<version>-win64-setup.exe -Algorithm SHA256`.
3. Запустите installer без административных прав.
4. Откройте приложение через Start Menu и завершите мастер первой настройки.

Frozen executable также принимает эксплуатационные CLI-команды:

```powershell
& "$env:LOCALAPPDATA\Programs\TutorAssistant\TutorAssistant.exe" doctor
& "$env:LOCALAPPDATA\Programs\TutorAssistant\TutorAssistant.exe" recovery-drill
```

По умолчанию используются независимые каталоги:

| Назначение | Расположение |
| --- | --- |
| Программа | `%LOCALAPPDATA%\Programs\TutorAssistant` |
| Конфигурация | `%APPDATA%\TutorAssistant` |
| Workspace по умолчанию | `%LOCALAPPDATA%\TutorAssistant` |
| Резервные копии | `<workspace>\backups` |
| Журналы | `<workspace>\logs` |

Рабочий каталог можно изменить в мастере настройки. Он никогда не должен совпадать
с каталогом установленной программы.

## Portable

Распакуйте `TutorAssistant-<version>-win64-portable.zip` в доступный для записи каталог.
Файл `portable.mode` рядом с `TutorAssistant.exe` явно разрешает использовать:

```text
TutorAssistant/
├── TutorAssistant.exe
├── portable.mode
├── config/
└── data/
```

Без `portable.mode` frozen executable использует installer-разделение program/config/workspace.
Не запускайте portable-версию из защищённого `Program Files` или каталога без записи.

## Исходники

```powershell
git clone https://github.com/ArtemLevin/tutor-assistant.git
cd tutor-assistant
uv sync --extra desktop --extra transcription --group dev
uv run python scripts\bootstrap.py
uv run tutor-assistant-gui config\app.yaml
```

Для сборки релиза дополнительно нужен `--extra packaging`. FFmpeg рекомендуется,
но базовое восстановление WAV работает без него. TeX Live/`latexmk`, Poppler,
Ollama и веса моделей устанавливаются отдельно при использовании соответствующего workflow.

## Обновление и удаление

Новая версия устанавливается поверх предыдущей. Installer не должен перезаписывать
пользовательскую конфигурацию, SQLite, архив занятий или backup-копии. Удаление
приложения удаляет program files, но сохраняет workspace, конфигурацию и backups.
Удалять пользовательские данные можно только отдельным явно подтверждённым действием.

Перед крупным обновлением можно создать защищённую копию вручную:

```powershell
tutor-assistant --config config\app.yaml content-backup --create --reason pre-upgrade
```
