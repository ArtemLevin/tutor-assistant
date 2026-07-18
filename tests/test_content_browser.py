from __future__ import annotations

from datetime import date
from pathlib import Path

from tutor_assistant.content import (
    AssetKind,
    LessonAsset,
    LessonContent,
    LessonPage,
    TranscriptRevision,
)
from tutor_assistant.content_browser import (
    content_file_rows,
    format_size,
    is_audio_path,
    pagination_text,
)
from tutor_assistant.domain import Lesson, Student


def make_content(workspace: Path) -> LessonContent:
    lesson = Lesson(
        lesson_id="lesson-one",
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 18),
        topic="Функции",
    )
    lesson.source_audio_local = str(workspace / "lessons" / lesson.lesson_id / "recording" / "lesson.wav")
    lesson.artifacts.cleaned_transcript = str(
        workspace / "lessons" / lesson.lesson_id / "transcript" / "cleaned.txt"
    )
    return LessonContent(
        lesson=lesson,
        assets=[
            LessonAsset(
                lesson_id=lesson.lesson_id,
                kind=AssetKind.AUDIO,
                relative_path="lessons/lesson-one/recording/lesson.wav",
                size_bytes=2048,
                sha256="a" * 64,
            )
        ],
        transcript=TranscriptRevision(
            lesson_id=lesson.lesson_id,
            revision_number=1,
            relative_path="lessons/lesson-one/transcript/cleaned.txt",
            content="Текст",
            content_sha256="b" * 64,
        ),
    )


def test_file_rows_deduplicate_registered_and_known_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    audio = workspace / "lessons" / "lesson-one" / "recording" / "lesson.wav"
    transcript = workspace / "lessons" / "lesson-one" / "transcript" / "cleaned.txt"
    audio.parent.mkdir(parents=True)
    transcript.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    transcript.write_text("Текст", encoding="utf-8")

    rows = content_file_rows(make_content(workspace), workspace)

    assert [item.display_path for item in rows].count("lessons/lesson-one/recording/lesson.wav") == 1
    audio_row = next(item for item in rows if item.kind == "audio")
    assert audio_row.exists
    assert audio_row.registered
    assert audio_row.size_bytes == 2048


def test_file_rows_mark_missing_and_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    content = make_content(workspace)
    content.lesson.latex.pdf_path = str(tmp_path / "outside.pdf")

    rows = content_file_rows(content, workspace)

    lesson_json = next(item for item in rows if item.display_path.endswith("lesson.json"))
    outside = next(item for item in rows if item.display_path.endswith("outside.pdf"))
    assert lesson_json.state_label == "Файл отсутствует"
    assert outside.state_label == "Вне каталога данных"
    assert outside.absolute_path is None


def test_pagination_and_size_labels() -> None:
    page = LessonPage(items=[], total=0, limit=50, offset=0)
    assert pagination_text(page) == "Занятия не найдены"
    assert format_size(2048) == "2.0 КБ"
    assert is_audio_path(Path("lesson.WAV"))
    assert not is_audio_path(Path("lesson.pdf"))
