from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one README match, found {count}: {old!r}")
    return text.replace(old, new, 1)


def main() -> None:
    text = README.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "К текущему состоянию завершены P0-стабилизация и **Wave 2 / Slices 1–10**:",
        "К текущему состоянию завершены P0-стабилизация и **Wave 2 / Slices 1–11**:",
    )
    text = replace_once(
        text,
        "- Qt-free `RecordingHealthMonitor` и typed health assessment для stream errors, callback timeout, silence и dropped-block policy.\n",
        "- Qt-free `RecordingHealthMonitor` и typed health assessment для stream errors, callback timeout, silence и dropped-block policy.\n"
        "- Qt-free `recording_presentation` model для duration, level normalization, health summary, warning/recovery cues и canonical recording-panel phases.\n",
    )
    text = replace_once(
        text,
        "Следующий архитектурный шаг — **Wave 2 / Slice 11: Recording presentation extraction**. Теперь runtime health policy уже application-owned; следующий slice должен вынести из base UI оставшееся форматирование и visual state recording panel. Детали и критерии готовности описаны в [`PLAN.md`](PLAN.md).",
        "Следующий архитектурный шаг — **Wave 2 / Slice 12: Production composition cleanup**. Recording policy и presentation state уже отделены от base UI; следующий slice упрощает production MRO/composition root и удаляет ставшие ненужными compatibility bridges. Детали и критерии готовности описаны в [`PLAN.md`](PLAN.md).",
    )
    text = replace_once(
        text,
        "На текущем этапе приоритет — завершить Wave 2, вынеся оставшийся recording presentation state из god-object `ui/app.py`, затем очистить production composition и перейти к декомпозиции transcription, normalization, LaTeX и shutdown orchestration.",
        "На текущем этапе приоритет — завершить Wave 2 через production composition cleanup, затем перейти к декомпозиции transcription, normalization, LaTeX и shutdown orchestration.",
    )
    README.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
