from pathlib import Path

path = Path("README.md")
text = path.read_text(encoding="utf-8")

intro_anchor = (
    "Проект рассчитан на Windows, PowerShell и локальную обработку аудио. "
    "Аудиозаписи остаются на компьютере преподавателя. В репозиторий учеников "
    "передаются подтверждённый транскрипт и метаданные задания.\n"
)
current_section = r'''

## Текущее состояние архитектуры — 18 августа 2026

Production GUI запускается через `tutor-assistant-gui`, который указывает на
`tutor_assistant.ui.recording_recovery_app:main`. Базовый `ui/app.py` остаётся
presentation shell и набором command ports; safety-critical recording orchestration
последовательно вынесена в application layer и production adapters.

К текущему состоянию завершены P0-стабилизация и **Wave 2 / Slices 1–9**:

- Qt-free `RecordingWorkflowController`;
- application-owned `StartRecordingUseCase`;
- application-owned stop/finalize и recovery semantics;
- физическое удаление legacy start/stop/recovery orchestration из base UI;
- application-owned Audio Preflight и удаление legacy preflight callbacks;
- `RecordingRuntimeRecorder` + read-only levels/health ports вместо concrete `DualRecorder` в base UI;
- `RefreshAudioDevicesUseCase` для discovery/resolution/hot-plug;
- stable microphone identity, переживающая PortAudio reindex и предпочитающая WASAPI;
- отсутствие direct `sounddevice` / `soundcard` discovery в base `ui/app.py`.

Архитектурный принцип текущей ветки разработки:

```text
Qt presentation / production composition
                ↓
application use cases + structural ports
                ↓
domain / pipeline services
                ↓
recording, persistence and external infrastructure
```

Следующий этап — **Wave 2 / Slice 10: Recording Runtime Health / Warning Policy extraction**.
Подробный статус завершённых этапов, границы следующего slice, тестовый контракт и дальнейший roadmap
зафиксированы в [`PLAN.md`](PLAN.md).
'''
if "## Текущее состояние архитектуры — 18 августа 2026" not in text:
    if intro_anchor not in text:
        raise SystemExit("README intro anchor not found")
    text = text.replace(intro_anchor, intro_anchor + current_section, 1)

old_setup = r'''Повторный запуск мастера:

```powershell
make setup
```

или:

```powershell
python -m tutor_assistant.ui.app
```

Если конфигурация лежит в другом месте:

```powershell
python -m tutor_assistant.ui.app C:\path\to\app.yaml
```
'''
new_setup = r'''Повторный запуск мастера:

```powershell
make setup
```

или через production entrypoint:

```powershell
uv run --all-extras tutor-assistant-gui --setup config\app.yaml
```

Если конфигурация лежит в другом месте:

```powershell
uv run --all-extras tutor-assistant-gui C:\path\to\app.yaml
```

Не используйте `python -m tutor_assistant.ui.app` как production entrypoint: базовый модуль
содержит общий presentation shell, а recording start/stop/recovery/preflight реализованы
production adapters поверх него.
'''
if old_setup not in text:
    raise SystemExit("README setup block not found")
text = text.replace(old_setup, new_setup, 1)

old_pr = r'''При доступном GitHub CLI приложение создаёт draft PR. Проверка и авторизация:

```powershell
gh auth status
gh auth login
```

URL сохраняется в `lesson.json`; открыть PR можно на вкладке публикации.
'''
new_pr = r'''Draft PR создаётся через GitHub API при настроенном `repository_full_name` и доступных
учётных данных. GitHub CLI больше не является обязательной зависимостью production workflow;
его можно использовать как дополнительный диагностический инструмент:

```powershell
gh auth status
```

URL сохраняется в `lesson.json`; открыть PR можно на вкладке публикации.
'''
if old_pr not in text:
    raise SystemExit("README GitHub CLI block not found")
text = text.replace(old_pr, new_pr, 1)

old_limits = r'''## Ограничения версии 0.7.0

- устройство воспроизведения должно быть подключено до запуска приложения;
- после подключения или отключения гарнитуры перезапустите Tutor Assistant для обновления списка;
- коррекция дрейфа автоматически применяется для расхождений до двух секунд; более крупные
  расхождения фиксируются в отчёте и требуют проверки устройств;
- спорные формулы проверяются преподавателем в сегментном редакторе;
- автоматический draft PR требует установленный и авторизованный GitHub CLI;
- визуальная проверка сложной вёрстки остаётся за преподавателем; приложение создаёт PNG-предпросмотр;
- Scheduled task обнаруживает задания при периодической проверке; немедленный внешний запуск
  ChatGPT Work через HTTP отсутствует.
'''
new_limits = r'''## Актуальные ограничения

- Windows/PortAudio может физически потерять endpoint при отключении USB-гарнитуры; перед preflight и
  стартом приложение заново выполняет discovery/resolution и при необходимости просит выбрать устройство;
  перезапуск Tutor Assistant для обычного hot-plug больше не является штатным требованием;
- stable microphone identity использует имя и host API, поэтому numeric PortAudio index может меняться
  между подключениями и автоматически переопределяется;
- коррекция дрейфа автоматически применяется для расхождений до двух секунд; более крупные
  расхождения фиксируются в отчёте и требуют проверки устройств;
- спорные формулы проверяются преподавателем в сегментном редакторе;
- draft PR требует доступных GitHub credentials и корректного `repository_full_name`; `gh` CLI необязателен;
- визуальная проверка сложной вёрстки остаётся за преподавателем; приложение создаёт PNG-предпросмотр;
- Scheduled task обнаруживает задания при периодической проверке; немедленный внешний запуск
  ChatGPT Work через HTTP отсутствует.
'''
if old_limits not in text:
    raise SystemExit("README limitations block not found")
text = text.replace(old_limits, new_limits, 1)

required = [
    "PLAN.md",
    "recording_recovery_app:main",
    "RefreshAudioDevicesUseCase",
    "RecordingRuntimeRecorder",
    "## Актуальные ограничения",
]
for marker in required:
    if marker not in text:
        raise SystemExit(f"README marker missing after rewrite: {marker}")

path.write_text(text, encoding="utf-8")
