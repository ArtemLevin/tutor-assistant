from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_base_ui() -> None:
    path = ROOT / "src/tutor_assistant/ui/app.py"
    text = path.read_text(encoding="utf-8")

    import_anchor = "from .playback import QtPlaybackBackend, QtStopScheduler\n"
    presentation_import = (
        "from .recording_presentation import (\n"
        "    RecordingPanelPhase,\n"
        "    RecordingTickPresentation,\n"
        "    build_recording_tick_presentation,\n"
        "    recording_panel_visual,\n"
        ")\n"
    )
    if presentation_import not in text:
        if text.count(import_anchor) != 1:
            raise RuntimeError("base UI playback import anchor is not unique")
        text = text.replace(import_anchor, import_anchor + presentation_import, 1)

    old_ready = (
        '        self.recording_state_label = QLabel("ГОТОВО К ЗАПИСИ")\n'
        '        self.recording_state_label.setObjectName("recordingState")\n'
    )
    new_ready = (
        "        ready_visual = recording_panel_visual(RecordingPanelPhase.READY)\n"
        "        self.recording_state_label = QLabel(ready_visual.text)\n"
        '        self.recording_state_label.setObjectName("recordingState")\n'
        '        self.recording_state_label.setProperty("active", ready_visual.active)\n'
    )
    if old_ready not in text:
        raise RuntimeError("base UI ready recording label anchor not found")
    text = text.replace(old_ready, new_ready, 1)

    start = text.index("    def _tick(self) -> None:\n")
    end = text.index("\n\n\ndef main() -> None:", start)
    replacement = '''    def _apply_recording_tick_presentation(
        self,
        presentation: RecordingTickPresentation,
    ) -> None:
        self.duration.setText(presentation.duration_text)
        if presentation.microphone_level_percent is not None:
            self.mic_level.setValue(presentation.microphone_level_percent)
        if presentation.system_level_percent is not None:
            self.system_level.setValue(presentation.system_level_percent)
        if presentation.health_text is not None:
            self.recording_health_label.setText(presentation.health_text)
        if presentation.status_message is not None and presentation.status_tone is not None:
            self._set_status(presentation.status_message, presentation.status_tone)
        if presentation.warning_log is not None:
            logging.warning("Контроль записи: %s", presentation.warning_log)

    def _set_recording_panel_phase(self, phase: RecordingPanelPhase) -> None:
        visual = recording_panel_visual(phase)
        self.recording_state_label.setText(visual.text)
        self.recording_state_label.setProperty("active", visual.active)
        refresh_style(self.recording_state_label)

    def _tick(self) -> None:
        self.recording_seconds += 1
        assessment = None
        if self.recorder and self.recorder.active:
            assessment = self.recording_health_monitor.assess(
                RecordingHealthSample.from_runtime(
                    elapsed_seconds=self.recording_seconds,
                    levels=self.recorder.levels,
                    health=self.recorder.health,
                )
            )
        presentation = build_recording_tick_presentation(
            self.recording_seconds,
            assessment,
        )
        self._apply_recording_tick_presentation(presentation)
        if assessment is not None and assessment.action == RecordingHealthAction.STOP:
            self._stop_recording_async(
                assessment.stop_reason or "Контроль записи запросил безопасную остановку"
            )
            return
'''
    text = text[:start] + replacement + text[end:]
    path.write_text(text, encoding="utf-8")


def patch_start_adapter() -> None:
    path = ROOT / "src/tutor_assistant/ui/audio_resilient_app.py"
    text = path.read_text(encoding="utf-8")

    import_anchor = "from . import app as base_app\n"
    phase_import = "from .recording_presentation import RecordingPanelPhase\n"
    if phase_import not in text:
        if text.count(import_anchor) != 1:
            raise RuntimeError("start adapter base import anchor is not unique")
        text = text.replace(import_anchor, import_anchor + phase_import, 1)
    text = text.replace("from .theme import refresh_style\n", "")

    old = (
        '        self.recording_state_label.setText("●  ИДЁТ ЗАПИСЬ")\n'
        '        self.recording_state_label.setProperty("active", True)\n'
        "        refresh_style(self.recording_state_label)\n"
    )
    new = "        self._set_recording_panel_phase(RecordingPanelPhase.RECORDING)\n"
    if text.count(old) != 1:
        raise RuntimeError("start adapter recording label block not found exactly once")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_finalize_adapter() -> None:
    path = ROOT / "src/tutor_assistant/ui/recording_finalize_app.py"
    text = path.read_text(encoding="utf-8")

    import_anchor = "from .audio_resilient_app import MainWindow as AudioResilientMainWindow\n"
    phase_import = "from .recording_presentation import RecordingPanelPhase\n"
    if phase_import not in text:
        if text.count(import_anchor) != 1:
            raise RuntimeError("finalize adapter import anchor is not unique")
        text = text.replace(import_anchor, import_anchor + phase_import, 1)
    text = text.replace("from .theme import refresh_style\n", "")

    replacements = (
        (
            '        self.recording_state_label.setText("СОХРАНЯЮ ЗАПИСЬ…")\n',
            "        self._set_recording_panel_phase(RecordingPanelPhase.SAVING)\n",
        ),
        (
            '        self.recording_state_label.setText("ЗАПИСЬ СОХРАНЕНА")\n'
            '        self.recording_state_label.setProperty("active", False)\n'
            "        refresh_style(self.recording_state_label)\n",
            "        self._set_recording_panel_phase(RecordingPanelPhase.SAVED)\n",
        ),
        (
            '        self.recording_state_label.setText("ЗАПИСЬ ТРЕБУЕТ ВОССТАНОВЛЕНИЯ")\n'
            '        self.recording_state_label.setProperty("active", False)\n'
            "        refresh_style(self.recording_state_label)\n",
            "        self._set_recording_panel_phase(RecordingPanelPhase.RECOVERY_REQUIRED)\n",
        ),
        (
            '        self.recording_state_label.setText("ЗАПИСЬ СОХРАНЕНА С ОШИБКОЙ")\n'
            '        self.recording_state_label.setProperty("active", False)\n'
            "        refresh_style(self.recording_state_label)\n",
            "        self._set_recording_panel_phase(RecordingPanelPhase.FAILED)\n",
        ),
    )
    for old, new in replacements:
        if text.count(old) != 1:
            raise RuntimeError(f"finalize adapter label block not found exactly once: {old!r}")
        text = text.replace(old, new, 1)

    path.write_text(text, encoding="utf-8")


def assert_boundaries() -> None:
    base = (ROOT / "src/tutor_assistant/ui/app.py").read_text(encoding="utf-8")
    tick_start = base.index("    def _tick(self) -> None:\n")
    tick_end = base.index("\n\n\ndef main() -> None:", tick_start)
    tick = base[tick_start:tick_end]
    for forbidden in (
        "divmod(",
        "self.duration.setText(",
        "self.mic_level.setValue(",
        "self.system_level.setValue(",
        "self.recording_health_label.setText(",
        "assessment.warning_changed",
        "assessment.recovered_from_warning",
        "assessment.warning_text",
    ):
        if forbidden in tick:
            raise RuntimeError(f"presentation logic remains in _tick: {forbidden}")

    start_adapter = (ROOT / "src/tutor_assistant/ui/audio_resilient_app.py").read_text(
        encoding="utf-8"
    )
    finalize_adapter = (
        ROOT / "src/tutor_assistant/ui/recording_finalize_app.py"
    ).read_text(encoding="utf-8")
    if "recording_state_label.setText" in start_adapter:
        raise RuntimeError("start adapter still formats recording state label")
    if "recording_state_label.setText" in finalize_adapter:
        raise RuntimeError("finalize adapter still formats recording state label")
    if "refresh_style" in start_adapter or "refresh_style" in finalize_adapter:
        raise RuntimeError("recording adapters still own recording label styling")


def main() -> None:
    patch_base_ui()
    patch_start_adapter()
    patch_finalize_adapter()
    assert_boundaries()


if __name__ == "__main__":
    main()
