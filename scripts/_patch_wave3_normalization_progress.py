from __future__ import annotations

from pathlib import Path


PRESENTATION = Path("src/tutor_assistant/ui/normalization_presentation.py")
APP = Path("src/tutor_assistant/ui/app.py")
TEST = Path("tests/test_normalization_presentation.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


presentation = PRESENTATION.read_text(encoding="utf-8")
presentation = replace_once(
    presentation,
    "class NormalizationProcessPresentation:\n"
    "    title: str\n"
    "    detail: str\n"
    "    tone: str = \"neutral\"\n"
    "    show_progress: bool = False\n",
    "class NormalizationProcessPresentation:\n"
    "    title: str\n"
    "    detail: str\n"
    "    tone: str = \"neutral\"\n"
    "    show_progress: bool = False\n"
    "    progress_total: int | None = None\n"
    "    progress_completed: int | None = None\n",
    "process progress fields",
)
presentation = replace_once(
    presentation,
    "            process=NormalizationProcessPresentation(\n"
    "                \"LLM-фильтрация выполняется\",\n"
    "                normalization_progress_detail(context.progress),\n"
    "                \"working\",\n"
    "                True,\n"
    "            ),\n",
    "            process=NormalizationProcessPresentation(\n"
    "                \"LLM-фильтрация выполняется\",\n"
    "                normalization_progress_detail(context.progress),\n"
    "                \"working\",\n"
    "                True,\n"
    "                context.progress.total_chunks if context.progress else None,\n"
    "                context.progress.completed_chunks if context.progress else None,\n"
    "            ),\n",
    "running progress values",
)
PRESENTATION.write_text(presentation, encoding="utf-8")

app = APP.read_text(encoding="utf-8")
app = replace_once(
    app,
    "        self.transcript_workspace.set_process_state(\n"
    "            presentation.process.title,\n"
    "            presentation.process.detail,\n"
    "            tone=presentation.process.tone,\n"
    "            show_progress=presentation.process.show_progress,\n"
    "        )\n\n"
    "    def normalize_current_transcript(\n",
    "        if presentation.process.progress_total is not None:\n"
    "            self.transcript_workspace.set_progress(\n"
    "                total=presentation.process.progress_total,\n"
    "                completed=presentation.process.progress_completed or 0,\n"
    "                title=presentation.process.title,\n"
    "                detail=presentation.process.detail,\n"
    "            )\n"
    "        else:\n"
    "            self.transcript_workspace.set_process_state(\n"
    "                presentation.process.title,\n"
    "                presentation.process.detail,\n"
    "                tone=presentation.process.tone,\n"
    "                show_progress=presentation.process.show_progress,\n"
    "            )\n\n"
    "    def normalize_current_transcript(\n",
    "progress rendering",
)
APP.write_text(app, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
test = replace_once(
    test,
    "    assert view.process.show_progress is True\n"
    "    assert view.process.detail == (\n",
    "    assert view.process.show_progress is True\n"
    "    assert view.process.progress_total == 4\n"
    "    assert view.process.progress_completed == 1\n"
    "    assert view.process.detail == (\n",
    "progress presentation assertions",
)
TEST.write_text(test, encoding="utf-8")
