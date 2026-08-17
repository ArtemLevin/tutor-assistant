from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src/tutor_assistant/ui/app.py"
WORKFLOW = ROOT / ".github/workflows/_wave2_remove_legacy_recording.yml"
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


def main() -> None:
    text = APP.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "from ..recording import (\n"
        "    DualRecorder,\n"
        "    SystemAudioSource,\n"
        "    find_recoverable_recordings,\n"
        "    list_input_devices,\n"
        "    list_system_audio_sources,\n"
        "    recover_recording,\n"
        ")\n",
        "from ..recording import (\n"
        "    DualRecorder,\n"
        "    SystemAudioSource,\n"
        "    list_input_devices,\n"
        "    list_system_audio_sources,\n"
        ")\n",
        label="recording imports",
    )
    text = replace_once(
        text,
        "        self._recovery_sessions: list[Path] = []\n",
        "",
        label="legacy recovery state",
    )

    old_builder = '''    def _make_lesson(self) -> Lesson:\n        if not self.topic.text().strip():\n            raise ValueError("Укажите тему занятия")\n        selected = next(item for item in self.students if item.id == self.student.currentData())\n        value = self.lesson_date.date()\n        lesson = Lesson(\n            student=selected,\n            subject=subject_value(self.subject.currentData() or self.subject.currentText()),\n            topic=self.topic.text().strip(),\n            lesson_date=date(value.year(), value.month(), value.day()),\n        )\n        self.pipeline.create(lesson)\n        return lesson\n\n'''
    new_builder = '''    def _build_lesson_from_form(self) -> Lesson:\n        """Build a Lesson draft from the shared form without persisting it."""\n\n        if not self.topic.text().strip():\n            raise ValueError("Укажите тему занятия")\n        selected = next(item for item in self.students if item.id == self.student.currentData())\n        value = self.lesson_date.date()\n        return Lesson(\n            student=selected,\n            subject=subject_value(self.subject.currentData() or self.subject.currentText()),\n            topic=self.topic.text().strip(),\n            lesson_date=date(value.year(), value.month(), value.day()),\n        )\n\n    def _create_lesson_from_form(self) -> Lesson:\n        """Persist a form-backed Lesson for non-recording workflows such as import."""\n\n        lesson = self._build_lesson_from_form()\n        self.pipeline.create(lesson)\n        return lesson\n\n'''
    text = replace_once(text, old_builder, new_builder, label="lesson builder")

    command_ports = '''    def start_recording(self) -> None:\n        """Command port implemented by the production recording-start adapter."""\n\n        raise NotImplementedError(\n            "Recording start is owned by the production application adapter"\n        )\n\n    def stop_recording(self) -> None:\n        self._stop_recording_async()\n\n    def _stop_recording_async(self, reason: str | None = None) -> None:\n        """Command port implemented by the production stop/finalize adapter."""\n\n        del reason\n        raise NotImplementedError(\n            "Recording stop/finalization is owned by the production application adapter"\n        )\n\n'''
    text = replace_between(
        text,
        "    def start_recording(self) -> None:\n",
        "    def choose_audio(self) -> None:\n",
        command_ports,
        label="legacy start/stop/finalize orchestration",
    )

    recovery_hook = '''    def _offer_recovery(self) -> None:\n        """Startup hook overridden by the production recording-recovery adapter."""\n\n        return\n\n'''
    text = replace_between(
        text,
        "    def _offer_recovery(self) -> None:\n",
        "    def _offer_unfinished_job(self) -> None:\n",
        recovery_hook,
        label="legacy recovery orchestration",
    )

    text = replace_once(
        text,
        "                self.lesson = self._make_lesson()\n",
        "                self.lesson = self._create_lesson_from_form()\n",
        label="manual transcription lesson creation",
    )

    forbidden = {
        "legacy lesson factory": "_make_lesson",
        "legacy recovery discovery": "find_recoverable_recordings",
        "legacy recovery execution": "recover_recording",
        "legacy stop worker": "Worker(recorder.stop)",
        "legacy recording lease acquisition": 'acquire_activity(\n                "recording"',
        "legacy recording completion callback": "def _recording_ready(",
        "legacy recording implementation callback": "def _recording_ready_impl(",
        "legacy recording failure callback": "def _recording_stop_failed(",
        "legacy recovery callback": "def _recovery_ready(",
        "legacy recovery failure callback": "def _recovery_failed(",
    }
    for label, token in forbidden.items():
        if token in text:
            raise RuntimeError(f"{label} still present: {token}")

    APP.write_text(text, encoding="utf-8")

    # This helper exists only to perform one deterministic branch-local rewrite.
    # Remove both helper and workflow before the generated commit so main never
    # inherits migration machinery after the PR is squash-merged.
    WORKFLOW.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
