from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/_wave2_prune_recording_mro.yml"
SELF = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, *, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"{label}: start marker not found")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:start_index] + replacement + text[end_index:]


def update(path: Path, transform) -> None:
    original = path.read_text(encoding="utf-8")
    updated = transform(original)
    if updated == original:
        raise RuntimeError(f"No changes produced for {path}")
    path.write_text(updated, encoding="utf-8")


def prune_audio_resilient(text: str) -> str:
    text = replace_once(text, "from datetime import date\n", "", label="audio date import")
    text = replace_once(
        text,
        "from ..domain import JobStatus, Lesson\n",
        "from ..domain import Lesson\n",
        label="audio domain import",
    )
    text = replace_once(
        text,
        "from .localization import subject_value\n",
        "",
        label="audio localization import",
    )
    text = replace_between(
        text,
        "    def _build_recording_lesson(self) -> Lesson:\n",
        "    def _present_recording_started(\n",
        "",
        label="duplicate recording lesson builder",
    )
    text = replace_once(
        text,
        "            recording_lesson = self._build_recording_lesson()\n",
        "            recording_lesson = self._build_lesson_from_form()\n",
        label="shared lesson builder routing",
    )
    text = replace_between(
        text,
        "    def _stop_recording_async(self, reason: str | None = None) -> None:\n",
        "\n\ndef main() -> None:\n",
        "\n",
        label="dead audio-resilient recording callback bridge",
    )
    for token in (
        "_build_recording_lesson",
        "super()._stop_recording_async",
        "super()._recording_ready",
        "super()._recording_stop_failed",
        "super()._recovery_ready",
        "JobStatus",
        "subject_value",
    ):
        if token in text:
            raise RuntimeError(f"audio_resilient legacy token remains: {token}")
    return text


def prune_transcript_publication(text: str) -> str:
    text = replace_once(
        text,
        "from ..audio_files import finalize_readable_audio\n",
        "",
        label="transcript finalizer import",
    )
    text = replace_between(
        text,
        "    def start_recording(self) -> None:\n",
        "    def _queue_imported_audio(self, lesson: Lesson, audio: Path) -> None:\n",
        "",
        label="dead transcript recording callbacks",
    )
    for token in (
        "finalize_readable_audio",
        "def _recording_ready_impl(",
        "def _recording_ready(",
        "def _recording_stop_failed(",
        "def _recovery_ready(",
    ):
        if token in text:
            raise RuntimeError(f"transcript_publication legacy token remains: {token}")
    return text


def prune_concurrent(text: str) -> str:
    text = replace_between(
        text,
        "    def start_recording(self) -> None:\n",
        "    def _tick(self) -> None:\n",
        "",
        label="dead concurrent recording callback bridge",
    )
    for token in (
        "def _recording_ready_impl(",
        "def _recording_stop_failed(",
        "super()._stop_recording_async",
    ):
        if token in text:
            raise RuntimeError(f"concurrent legacy token remains: {token}")
    return text


def main() -> None:
    update(
        ROOT / "src/tutor_assistant/ui/audio_resilient_app.py",
        prune_audio_resilient,
    )
    update(
        ROOT / "src/tutor_assistant/ui/transcript_publication_app.py",
        prune_transcript_publication,
    )
    update(
        ROOT / "src/tutor_assistant/ui/concurrent_app.py",
        prune_concurrent,
    )

    WORKFLOW.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
