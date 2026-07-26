from __future__ import annotations

import json
from pathlib import Path

from tutor_assistant.normalization.models import NormalizedTranscript


def main() -> None:
    target = Path("schemas/transcript-normalized.schema.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            NormalizedTranscript.model_json_schema(),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
