from __future__ import annotations

from pathlib import Path


README = Path("README.md")
PLAN = Path("PLAN.md")

readme = README.read_text(encoding="utf-8")
anchor = (
    "- explicit production composition: общий bootstrap получает `window_type`, а production adapters "
    "больше не меняют `base_app.MainWindow` через module-global rebinding; responsibility-bearing MRO "
    "закреплён architecture tests.\n"
)
if readme.count(anchor) != 1:
    raise RuntimeError("README architecture anchor not found exactly once")
readme = readme.replace(
    anchor,
    anchor
    + "- Qt-free `TranscriptionQueueCoordinator` для restore/pump/retry/complete/fail decisions; queue UI получает typed snapshot, а `TranscriptionWorker` вынесен из `ui/app.py` в отдельный Qt transport adapter.\n",
    1,
)
old_next = (
    "**Wave 2 завершён.** Следующий архитектурный шаг — **Wave 3 / Slice 13: Transcription queue "
    "presentation/orchestration extraction**. Цель — вынести из `ui/app.py` координацию очереди "
    "транскрибации и её presentation state за Qt-free boundary, не затрагивая recording safety path. "
    "Детали и критерии готовности описаны в [`PLAN.md`](PLAN.md)."
)
new_next = (
    "**Wave 2 завершён, Wave 3 / Slice 13 также завершён.** Следующий архитектурный шаг — "
    "**Wave 3 / Slice 14: LLM normalization presentation/orchestration extraction**. Цель — отделить "
    "normalization scheduling, execution state и result transitions от base Qt window, сохранив explicit "
    "cloud consent, cancellation/resume и mutual exclusion с локальной Whisper-транскрибацией. Детали "
    "описаны в [`PLAN.md`](PLAN.md)."
)
if old_next not in readme:
    raise RuntimeError("README next-step paragraph not found")
readme = readme.replace(old_next, new_next, 1)
old_footer = (
    "На текущем этапе Wave 2 завершён. Приоритет Wave 3 — последовательно декомпозировать transcription "
    "queue, normalization, LaTeX и shutdown orchestration, начиная с очереди транскрибации."
)
new_footer = (
    "На текущем этапе Wave 2 и transcription-queue Slice 13 завершены. Приоритет Wave 3 — "
    "декомпозиция LLM normalization orchestration, затем LaTeX monitor, shutdown coordination и "
    "parallel-review synchronization."
)
if old_footer not in readme:
    raise RuntimeError("README roadmap footer not found")
README.write_text(readme.replace(old_footer, new_footer, 1), encoding="utf-8")

plan = PLAN.read_text(encoding="utf-8")
old_state = (
    "Wave 2 архитектурной стабилизации production-контура записи завершён. Критический recording path "
    "отделён от legacy orchestration базового Qt-окна, production composition сделана явной; проект "
    "переходит к Wave 3 — декомпозиции остальных зон `ui/app.py`."
)
new_state = (
    "Wave 2 архитектурной стабилизации production-контура записи завершён. В Wave 3 завершён первый "
    "slice: orchestration локальной очереди транскрибации отделена от базового Qt-окна. Следующий фокус — "
    "LLM normalization orchestration."
)
if old_state not in plan:
    raise RuntimeError("PLAN current-state paragraph not found")
plan = plan.replace(old_state, new_state, 1)
start = plan.find("## 4. Следующий шаг — Wave 3 / Slice 13\n")
end = plan.find("### Последующие Wave 3 slices\n", start)
if start < 0 or end < 0:
    raise RuntimeError("PLAN Slice 13 section markers not found")
replacement = '''## 4. Завершённый шаг — Wave 3 / Slice 13

### Transcription queue presentation/orchestration extraction

**Цель:** отделить управление локальной очередью транскрибации и её presentation state от базового Qt god-object, сохранив storage/retry/shutdown semantics и не меняя recording path.

Реализовано:

- новый Qt-free `application/transcription_queue.py` с `TranscriptionQueueCoordinator`;
- coordinator владеет restore/pump/retry/complete/fail/discard decisions поверх существующей `TranscriptionQueue` state machine;
- `TranscriptionQueueSnapshot` и entry snapshots дают UI immutable queue state без чтения widgets как source of truth;
- `ui/transcription_queue_presentation.py` централизует labels, row text/tooltips, counters и quick badge;
- `TranscriptionWorker` физически вынесен из `ui/app.py` в `ui/transcription_worker.py` как Qt transport adapter;
- `ui/app.py` делегирует restore/pump/retry/completion coordinator-у и только запускает worker/рендерит presentation;
- `concurrent_app.py` больше не дублирует Lesson transition, persistence и raw queue retry;
- normalization busy gates читают active transcription через coordinator, сохраняя CPU mutual exclusion Ollama ↔ Whisper;
- временная migration infrastructure удалена из итогового product diff.

Тестовый контур покрывает sequential pump, shutdown/normalization barriers, fail→next, retry и missing audio, persisted restore semantics, orphan recovery, snapshots, presentation formatting и architecture boundaries.

Финальный product diff: 9 файлов. Exact head прошёл Privacy History Gate и scaling 100/125/150/200%; перед merge получены независимые full-suite SUCCESS минимум на Python 3.12 и 3.14. PR #92 squash-merged в `main`.

## 5. Следующий шаг — Wave 3 / Slice 14

### LLM normalization presentation/orchestration extraction

**Цель:** отделить scheduling и lifecycle LLM-фильтрации от `ui/app.py`, сохранив ручное подтверждение результата, cancellation/resume, cloud-consent policy и запрет одновременной CPU-heavy Ollama/Whisper обработки.

План Slice 14:

1. проинвентаризировать `normalize_current_transcript`, `_pump_auto_normalization`, worker callbacks, resume-confirmation и control-state synchronization;
2. выделить Qt-free normalization coordinator/state model для start eligibility, queued auto-normalization, completion/failure/cancel/resume decisions;
3. оставить `NormalizationWorker` транспортным Qt adapter, не перенося concrete Ollama/Yandex providers в UI abstraction;
4. ввести typed presentation state для process status/primary action вместо распределённого форматирования по `ui/app.py`;
5. сохранить explicit Yandex cloud consent и невозможность auto-cloud processing без ручного согласия;
6. сохранить mutual exclusion с active Whisper queue через transcription coordinator contract;
7. добавить pure unit tests для manual start, auto queue, busy barriers, cancel, resume/indeterminate, success/failure и cloud-consent gates;
8. добавить architecture gates, не смешивая Slice 14 с LaTeX monitor или shutdown refactor.

### Definition of Done Slice 14

- normalization lifecycle decisions тестируются без PySide6;
- Qt widgets не являются source of truth для normalization execution state;
- Ollama/Whisper mutual exclusion сохранён;
- Yandex cloud processing по-прежнему требует explicit consent;
- cancellation/resume и manual review semantics не изменены;
- recording/transcription boundaries остаются неизменными;
- Windows CI, privacy gate и scaling matrix зелёные;
- минимум два независимых full-suite success;
- PR squash-merged в `main`.

'''
plan = plan[:start] + replacement + plan[end:]
plan = plan.replace(
    "### Последующие Wave 3 slices\n\nПосле Slice 13:\n\n1. LLM normalization presentation orchestration;\n2. LaTeX monitor UI orchestration;\n3. application shutdown coordinator;\n4. teacher cockpit / parallel review synchronization.\n",
    "### Последующие Wave 3 slices\n\nПосле Slice 14:\n\n1. LaTeX monitor UI orchestration;\n2. application shutdown coordinator;\n3. teacher cockpit / parallel review synchronization.\n",
    1,
)
plan = plan.replace("## 5. Инварианты разработки", "## 6. Инварианты разработки", 1)
plan = plan.replace("## 6. Рабочий порядок для следующих slices", "## 7. Рабочий порядок для следующих slices", 1)
plan = plan.replace(
    "8. получить минимум два независимых full-suite success для существенных recording changes;",
    "8. получить минимум два независимых full-suite success для существенных orchestration/refactor changes;",
    1,
)
PLAN.write_text(plan, encoding="utf-8")
