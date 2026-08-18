from __future__ import annotations

from pathlib import Path


PATH = Path("scripts/_migrate_wave3_transcription_queue.py")
text = PATH.read_text(encoding="utf-8")
anchor = '''if "self.transcription_queue." in app:\n    raise RuntimeError("raw transcription_queue mutation remains in app.py")\n'''
if text.count(anchor) != 1:
    raise RuntimeError("migration guard anchor not found exactly once")
replacements = '''app = replace_once(\n    app,\n    '        if provider == "ollama" and (self.transcription_worker.busy or self.transcription_queue.active):\\n',\n    '        if provider == "ollama" and (\\n'\n    '            self.transcription_worker.busy\\n'\n    '            or self.transcription_queue_coordinator.active\\n'\n    '        ):\\n',\n    "manual normalization busy gate",\n)\napp = replace_once(\n    app,\n    '                and (self.transcription_worker.busy or self.transcription_queue.active is not None)\\n',\n    '                and (\\n'\n    '                    self.transcription_worker.busy\\n'\n    '                    or self.transcription_queue_coordinator.active is not None\\n'\n    '                )\\n',\n    "automatic normalization busy gate",\n)\n\n'''
PATH.write_text(text.replace(anchor, replacements + anchor, 1), encoding="utf-8")
