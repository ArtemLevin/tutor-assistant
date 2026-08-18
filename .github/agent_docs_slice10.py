from pathlib import Path

README = Path('README.md')
PLAN = Path('PLAN.md')
WORKFLOW = Path('.github/workflows/agent_docs_slice10.yml')
SCRIPT = Path(__file__)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, got {count}')
    return text.replace(old, new, 1)


readme = README.read_text(encoding='utf-8')
readme = replace_once(
    readme,
    '**Wave 2 / Slices 1–9**',
    '**Wave 2 / Slices 1–10**',
    'README slice count',
)
readme = replace_once(
    readme,
    '- отсутствие direct `sounddevice` / `soundcard` discovery в base `ui/app.py`.\n',
    '- отсутствие direct `sounddevice` / `soundcard` discovery в base `ui/app.py`;\n'
    '- Qt-free `RecordingHealthMonitor` и typed health assessment для stream errors, callback timeout, silence и dropped-block policy.\n',
    'README health bullet',
)
readme = replace_once(
    readme,
    'Следующий архитектурный шаг — **Wave 2 / Slice 10: Recording Runtime Health / Warning Policy extraction**. Детали, критерии готовности и дальнейший порядок работ описаны в [`PLAN.md`](PLAN.md).',
    'Следующий архитектурный шаг — **Wave 2 / Slice 11: Recording presentation extraction**. Теперь runtime health policy уже application-owned; следующий slice должен вынести из base UI оставшееся форматирование и visual state recording panel. Детали и критерии готовности описаны в [`PLAN.md`](PLAN.md).',
    'README next step',
)
readme = replace_once(
    readme,
    'На текущем этапе приоритет — завершить Wave 2, вынеся runtime health/warning policy и оставшийся recording presentation state из god-object `ui/app.py`, а затем перейти к декомпозиции transcription, normalization, LaTeX и shutdown orchestration.',
    'На текущем этапе приоритет — завершить Wave 2, вынеся оставшийся recording presentation state из god-object `ui/app.py`, затем очистить production composition и перейти к декомпозиции transcription, normalization, LaTeX и shutdown orchestration.',
    'README priority',
)
README.write_text(readme, encoding='utf-8')

plan = PLAN.read_text(encoding='utf-8')
plan = replace_once(
    plan,
    '9. **Audio device discovery boundary** — hardware discovery/resolution/probe вынесены за base UI; production adapter использует `RefreshAudioDevicesUseCase`, а stable microphone identity централизована в нейтральном resolver.\n',
    '9. **Audio device discovery boundary** — hardware discovery/resolution/probe вынесены за base UI; production adapter использует `RefreshAudioDevicesUseCase`, а stable microphone identity централизована в нейтральном resolver.\n'
    '10. **Recording runtime health policy** — интерпретация stream errors, callback timeout, silence и dropped blocks вынесена из `_tick()` в Qt-free `RecordingHealthMonitor`; UI получает typed assessment и только отображает состояние/исполняет terminal stop action.\n',
    'PLAN completed slice 10',
)
plan = replace_once(
    plan,
    'На момент завершения Slice 9 production path проверен Windows CI на Python 3.11–3.14; для merge были получены независимые полные успешные regression runs, privacy gate и scaling matrix 100/125/150/200%.',
    'На момент завершения Slice 10 production path прошёл lint/compile/import/contracts на Windows matrix Python 3.11–3.14; перед merge получены независимые полные успешные regression runs на Python 3.12 и 3.14, а privacy gate и scaling matrix 100/125/150/200% были зелёными.',
    'PLAN CI status',
)
plan = replace_once(
    plan,
    '## 3. Следующий шаг — Wave 2 / Slice 10',
    '## 3. Завершённый шаг — Wave 2 / Slice 10',
    'PLAN slice10 heading',
)
plan = replace_once(
    plan,
    'Сейчас `_tick()` одновременно:',
    'До Slice 10 `_tick()` одновременно:',
    'PLAN historical tick',
)
plan = replace_once(
    plan,
    'Эта policy должна стать Qt-free и детерминированно тестироваться отдельно от GUI.',
    'Эта policy теперь Qt-free и детерминированно тестируется отдельно от GUI через `RecordingHealthMonitor`, `RecordingHealthPolicy`, `RecordingHealthSample` и `RecordingHealthAssessment`.',
    'PLAN policy status',
)
plan = replace_once(
    plan,
    '### Предлагаемая архитектура',
    '### Реализованная архитектура',
    'PLAN architecture heading',
)
plan = replace_once(
    plan,
    'Предлагаемые типы:',
    'Реализованные типы:',
    'PLAN type heading',
)
plan = replace_once(
    plan,
    '## 4. Последующие шаги',
    '## 4. Следующий шаг и последующие slices',
    'PLAN following heading',
)
plan = replace_once(
    plan,
    '### Wave 2 / Slice 11 — Recording presentation extraction',
    '### Следующий шаг — Wave 2 / Slice 11 — Recording presentation extraction',
    'PLAN slice11 heading',
)
PLAN.write_text(plan, encoding='utf-8')

# Keep migration-only files out of the final docs diff.
WORKFLOW.unlink()
SCRIPT.unlink()
