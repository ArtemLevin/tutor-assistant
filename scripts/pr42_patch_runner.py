from __future__ import annotations

import runpy
from pathlib import Path

source = Path(__file__).with_name("pr42_preserve_model_candidates.py")
temporary = Path(__file__).with_name(".pr42_preserve_model_candidates_runtime.py")
text = source.read_text(encoding="utf-8")
old = '''    if count != 1:\n        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:80]!r}")\n    target.write_text(text.replace(old, new, 1), encoding="utf-8")\n'''
new = '''    duplicated_result_anchor = (\n        path == "src/tutor_assistant/normalization/service.py"\n        and old == (\n            "                provider_requests=provider_requests,\\n"\n            "                source_fallback_chunks=source_fallback_chunks,\\n"\n        )\n        and count == 2\n    )\n    if count != 1 and not duplicated_result_anchor:\n        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:80]!r}")\n    target.write_text(text.replace(old, new, 1), encoding="utf-8")\n'''
if text.count(old) != 1:
    raise RuntimeError("Unable to patch PR42 replace_once guard")
text = text.replace(old, new, 1)
fixture_old = '    provider = FakeNormalizationProvider(default="")\n'
fixture_new = '    provider = FakeNormalizationProvider(responses=[""])\n'
if text.count(fixture_old) != 1:
    raise RuntimeError("Unable to patch explicit empty response fixture")
text = text.replace(fixture_old, fixture_new, 1)
temporary.write_text(text, encoding="utf-8")
try:
    runpy.run_path(str(temporary), run_name="__main__")
finally:
    temporary.unlink(missing_ok=True)
