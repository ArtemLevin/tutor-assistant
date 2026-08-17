from __future__ import annotations

import re
from pathlib import Path

APP = Path("src/tutor_assistant/ui/app.py")
text = APP.read_text(encoding="utf-8")

start_marker = "    def _begin_preflight(self, show_intro: bool) -> None:\n"
end_marker = "    def _play_preflight_track(self, source: str) -> None:\n"

start = text.index(start_marker)
end = text.index(end_marker, start)
legacy_block = text[start:end]

required_legacy_tokens = (
    "recorder = DualRecorder(",
    "sleep(seconds)",
    "def _device_test_ready(self, results) -> None:",
    "quality_report.read_text(encoding=\"utf-8\")",
)
for token in required_legacy_tokens:
    if token not in legacy_block:
        raise RuntimeError(f"Expected legacy preflight token is missing: {token}")

command_port = '''    def _begin_preflight(self, show_intro: bool) -> None:\n        del show_intro\n        raise NotImplementedError(\n            "Audio preflight is owned by the production audio application adapter"\n        )\n\n'''
text = text[:start] + command_port + text[end:]

# The removed legacy block was the only base-UI owner of these infrastructure dependencies.
text = text.replace("from time import sleep\n", "")
text = text.replace("    DualRecorder,\n", "")

for forbidden in (
    "recorder = DualRecorder(",
    "def _device_test_ready(self, results) -> None:",
    "sleep(seconds)",
):
    if forbidden in text:
        raise RuntimeError(f"Legacy preflight orchestration still present in app.py: {forbidden}")

if re.search(r"\\bDualRecorder\\b", text):
    raise RuntimeError("DualRecorder is still referenced by base UI after cleanup")
if re.search(r"\\bsleep\\(", text):
    raise RuntimeError("sleep() is still referenced by base UI after cleanup")
if "def _play_preflight_track(self, source: str) -> None:" not in text:
    raise RuntimeError("Preflight playback presentation hook was removed accidentally")
if "def test_devices(self) -> None:" not in text:
    raise RuntimeError("Device-test command presentation hook was removed accidentally")

APP.write_text(text, encoding="utf-8")
