from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "tutor_assistant" / "security" / "history_privacy.py"
MODULE_NAME = "tutor_assistant_history_privacy"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Не удалось загрузить history privacy module: {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    raise SystemExit(_load_module().main())
