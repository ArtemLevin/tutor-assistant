from __future__ import annotations

from pathlib import Path


README = Path("README.md")
PLAN = Path("PLAN.md")

readme = README.read_text(encoding="utf-8")
old_count = "К текущему состоянию завершены P0-стабилизация и **Wave 2 / Slices 1–11**:"
new_count = "К текущему состоянию завершены P0-стабилизация и **Wave 2 / Slices 1–12**:"
if old_count not in readme:
    raise RuntimeError("README Wave 2 count anchor not found")
readme = readme.replace(old_count, new_count, 1)

needle = (
    "- Qt-free `recording_presentation` model для duration, level normalization, "
    "health summary, warning/recovery cues и canonical recording-panel phases.\n"
)
if needle not in readme:
    raise RuntimeError("README Wave 2 bullet anchor not found")
readme = readme.replace(
    needle,
    needle
    + "- explicit production composition: общий bootstrap получает `window_type`, а production adapters больше не меняют `base_app.MainWindow` через module-global rebinding; responsibility-bearing MRO закреплён architecture tests.\n",
    1,
)

old_next = (
    "Следующий архитектурный шаг — **Wave 2 / Slice 12: Production composition cleanup**. "
    "Recording policy и presentation state уже отделены от base UI; следующий slice упрощает "
    "production MRO/composition root и удаляет ставшие ненужными compatibility bridges. "
    "Детали и критерии готовности описаны в [`PLAN.md`](PLAN.md)."
)
new_next = (
    "**Wave 2 завершён.** Следующий архитектурный шаг — **Wave 3 / Slice 13: "
    "Transcription queue presentation/orchestration extraction**. Цель — вынести из `ui/app.py` "
    "координацию очереди транскрибации и её presentation state за Qt-free boundary, не затрагивая "
    "recording safety path. Детали и критерии готовности описаны в [`PLAN.md`](PLAN.md)."
)
if old_next not in readme:
    raise RuntimeError("README next-step paragraph not found")
readme = readme.replace(old_next, new_next, 1)

old_bottom = (
    "На текущем этапе приоритет — завершить Wave 2 через production composition cleanup, затем "
    "перейти к декомпозиции transcription, normalization, LaTeX и shutdown orchestration."
)
new_bottom = (
    "На текущем этапе Wave 2 завершён. Приоритет Wave 3 — последовательно декомпозировать "
    "transcription queue, normalization, LaTeX и shutdown orchestration, начиная с очереди транскрибации."
)
if old_bottom not in readme:
    raise RuntimeError("README roadmap footer not found")
readme = readme.replace(old_bottom, new_bottom, 1)
README.write_text(readme, encoding="utf-8")

plan = PLAN.read_text(encoding="utf-8")
old_intro = (
    "Проект находится в фазе архитектурной стабилизации production-контура записи. "
    "Критический путь записи уже вынесен из legacy orchestration базового Qt-окна в application layer и production adapters."
)
new_intro = (
    "Wave 2 архитектурной стабилизации production-контура записи завершён. "
    "Критический recording path отделён от legacy orchestration базового Qt-окна, production composition сделана явной; проект переходит к Wave 3 — декомпозиции остальных зон `ui/app.py`."
)
if old_intro not in plan:
    raise RuntimeError("PLAN intro anchor not found")
plan = plan.replace(old_intro, new_intro, 1)

bullet11 = (
    "11. **Recording presentation extraction** — timer formatting, level normalization, health summary, "
    "warning/recovery presentation cues и канонические visual phases recording panel вынесены в Qt-free "
    "`ui/recording_presentation.py`; start/finalize adapters больше не форматируют recording-state label вручную.\n"
)
if bullet11 not in plan:
    raise RuntimeError("PLAN Slice 11 anchor not found")
plan = plan.replace(
    bullet11,
    bullet11
    + "12. **Production composition cleanup** — общий GUI bootstrap принимает explicit `window_type`; устранены module-global `base_app.MainWindow = MainWindow` rebinding во всех production adapters, при этом responsibility-bearing MRO и стабильный console entrypoint сохранены и закреплены architecture tests.\n",
    1,
)

old_ci = (
    "На момент завершения Slice 11 production path прошёл lint/compile/import/contracts на Windows matrix "
    "Python 3.11–3.14; перед merge получены независимые полные успешные regression runs на Python 3.12, "
    "3.13 и 3.14, а privacy gate и scaling matrix 100/125/150/200% были зелёными."
)
new_ci = (
    "На момент завершения Slice 12 exact feature head прошёл lint/compile/import/contracts на Windows matrix "
    "Python 3.11–3.14; перед merge получены независимые полные успешные regression runs минимум на Python 3.12 "
    "и 3.14, а privacy gate и scaling matrix 100/125/150/200% были зелёными."
)
if old_ci not in plan:
    raise RuntimeError("PLAN CI summary anchor not found")
plan = plan.replace(old_ci, new_ci, 1)

start_marker = "## 3. Завершённый шаг — Wave 2 / Slice 11"
end_marker = "## 5. Инварианты разработки"
start = plan.find(start_marker)
end = plan.find(end_marker)
if start < 0 or end < 0 or end <= start:
    raise RuntimeError("PLAN section boundaries not found")
replacement = """## 3. Завершённый шаг — Wave 2 / Slice 12

### Production composition cleanup

**Цель:** завершить recording-focused Wave 2 явным composition root без module-global подмены класса окна и без искусственного схлопывания слоёв, которые всё ещё несут самостоятельную ответственность.

Инвентаризация production MRO подтвердила, что все слои остаются содержательными:

```text
recording_recovery_app.MainWindow
→ recording_finalize_app.MainWindow
→ audio_resilient_app.MainWindow
→ transcript_publication_app.MainWindow
→ concurrent_app.MainWindow
→ ui.app.MainWindow
```

Ответственности остаются разделены:

- `concurrent_app` — background tasks и parallel-review coordination;
- `transcript_publication_app` — publication/cockpit presentation;
- `audio_resilient_app` — audio-device refresh, preflight и start recording;
- `recording_finalize_app` — stop/finalize и recovery-required outcome presentation;
- `recording_recovery_app` — discovery и восстановление незавершённых recording sessions.

Поэтому Slice 12 не удаляет эти классы. Вместо старого механизма:

```python
base_app.MainWindow = MainWindow
base_app.main()
```

общий bootstrap теперь принимает явный тип окна:

```python
def main(window_type: type[MainWindow] = MainWindow) -> None:
    ...
    window = window_type(config_path)
```

а каждый production adapter запускается через:

```python
base_app.main(MainWindow)
```

Console entrypoint остаётся прежним:

```text
tutor-assistant-gui = tutor_assistant.ui.recording_recovery_app:main
```

### Architecture gates Slice 12

`tests/test_production_composition.py` закрепляет:

- explicit `window_type` injection в общем bootstrap;
- отсутствие `base_app.MainWindow = MainWindow` во всех production composition modules;
- точный responsibility-bearing MRO;
- наличие собственной ответственности у каждого слоя;
- неизменность production console entrypoint;
- отсутствие временных migration files.

Первые CI-запуски выявили только `Ruff I001` в новом architecture test. Runtime diff не менялся; import formatting теста приведён к проектному стандарту, после чего свежий exact head прошёл ранние gates и требуемые full-suite проверки.

**Итог Wave 2:** recording lifecycle, start/stop/recovery, preflight, device discovery, runtime recorder contract, health policy, presentation mapping и production composition имеют явные границы и regression/architecture coverage. Дальнейшая декомпозиция должна идти уже по другим функциональным зонам `ui/app.py`.

## 4. Следующий шаг — Wave 3 / Slice 13

### Transcription queue presentation/orchestration extraction

**Цель:** отделить управление локальной очередью транскрибации и её presentation state от базового Qt god-object, не меняя storage semantics и не затрагивая завершённый recording path.

План Slice 13:

1. проинвентаризировать методы `ui/app.py` и `concurrent_app.py`, которые читают/изменяют `TranscriptionQueue`, запускают `TranscriptionWorker`, восстанавливают pending jobs и обновляют processing UI;
2. разделить domain/application orchestration и Qt rendering: queue transitions, retry/restore/pump decisions должны стать тестируемыми без widgets;
3. ввести компактные typed snapshots/actions для queue state вместо чтения Qt widgets как источника истины;
4. сохранить последовательную обработку, persisted pending jobs, retry semantics, cancellation/shutdown safety и current lesson status transitions;
5. не переносить Whisper transport/model implementation в UI-layer abstraction — concrete transcriber остаётся infrastructure/pipeline concern;
6. сократить base `ui/app.py` до command/rendering adapter для transcription queue;
7. добавить unit tests для pump/restore/retry/idle/busy/error paths и architecture gates против возврата orchestration в base UI;
8. не смешивать Slice 13 с LLM normalization или LaTeX monitor refactor.

### Definition of Done Slice 13

- queue orchestration тестируется без PySide6;
- Qt widgets не являются источником истины для queue state;
- persisted pending/retry semantics не изменены;
- startup restore и shutdown не теряют транскрипционные jobs;
- recording composition и safety contracts остаются неизменными;
- Windows CI, privacy gate и scaling matrix зелёные;
- существенный orchestration refactor получает минимум два независимых full-suite success;
- PR squash-merged в `main`.

### Последующие Wave 3 slices

После Slice 13:

1. LLM normalization presentation orchestration;
2. LaTeX monitor UI orchestration;
3. application shutdown coordinator;
4. teacher cockpit / parallel review synchronization.

Каждый этап следует тому же правилу: сначала выделяется реальная policy/orchestration в Qt-free boundary, затем production path переключается на неё, после чего dead legacy-код физически удаляется.

"""
plan = plan[:start] + replacement + plan[end:]
PLAN.write_text(plan, encoding="utf-8")

Path("scripts/_update_wave2_docs.py").unlink()
Path(".github/workflows/_update_wave2_docs.yml").unlink()
