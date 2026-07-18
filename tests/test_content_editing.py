from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tutor_assistant.content import (
    LessonEditConflictError,
    StudentContentService,
    TranscriptEditConflictError,
)
from tutor_assistant.domain import (
    GeneratedMaterial,
    LatexState,
    Lesson,
    PublicationInfo,
    Student,
)


def make_published_lesson() -> Lesson:
    return Lesson(
        lesson_id="editable-lesson",
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 18),
        topic="Редактируемое занятие",
        publication=PublicationInfo(
            branch="lesson/editable",
            repository_path="students/student/lesson",
            commit="abc123",
            pr_url="https://github.com/example/repo/pull/1",
        ),
        latex=LatexState(
            tex_path="handbook/lesson.tex",
            pdf_path="handbook/lesson.pdf",
            tex_blob_sha="blob123",
        ),
    )


def test_autosaved_draft_survives_optimistic_conflict(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    lesson = service.create_lesson(make_published_lesson())
    first = service.save_transcript(
        lesson.lesson_id,
        "Первая версия",
        expected_revision_number=None,
    )
    draft = service.save_transcript_draft(
        lesson.lesson_id,
        "Моя незавершённая правка",
        base_revision_number=first.revision_number,
    )

    competing = service.save_transcript(
        lesson.lesson_id,
        "Изменение из другого окна",
        expected_revision_number=first.revision_number,
    )
    with pytest.raises(TranscriptEditConflictError) as error:
        service.save_transcript(
            lesson.lesson_id,
            draft.content,
            expected_revision_number=draft.base_revision_number,
        )

    content = service.get_lesson(lesson.lesson_id)
    assert (error.value.expected, error.value.current) == (1, 2)
    assert content.transcript == competing
    assert content.draft is not None
    assert content.draft.content == "Моя незавершённая правка"
    transcript_path = Path(content.lesson.artifacts.verified_transcript or "")
    assert transcript_path.read_text(encoding="utf-8") == "Изменение из другого окна\n"


def test_transcript_history_compare_and_restore_are_append_only(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    lesson = service.create_lesson(make_published_lesson())
    first = service.save_transcript(
        lesson.lesson_id,
        "Первая строка",
        expected_revision_number=None,
    )
    service.save_transcript_draft(
        lesson.lesson_id,
        "Вторая строка",
        base_revision_number=first.revision_number,
    )
    second = service.save_transcript(
        lesson.lesson_id,
        "Вторая строка",
        expected_revision_number=first.revision_number,
    )

    difference = service.compare_transcript_revisions(first.id, second.id)
    restored = service.revert_transcript(
        first.id,
        expected_revision_number=second.revision_number,
    )
    revisions = service.list_transcript_revisions(lesson.lesson_id)

    assert "-Первая строка" in difference
    assert "+Вторая строка" in difference
    assert restored.revision_number == 3
    assert restored.content == "Первая строка\n"
    assert [revision.revision_number for revision in revisions] == [3, 2, 1]
    assert service.get_lesson(lesson.lesson_id).draft is None


def test_transcript_edit_preserves_publication_and_marks_generated_materials_stale(
    tmp_path: Path,
) -> None:
    service = StudentContentService(tmp_path / "data")
    original = service.create_lesson(make_published_lesson())

    revision = service.save_transcript(
        original.lesson_id,
        "Подтверждённый текст",
        expected_revision_number=None,
    )
    updated = service.get_lesson(original.lesson_id).lesson

    assert updated.publication == original.publication
    assert updated.latex == original.latex
    assert updated.stale_materials == [GeneratedMaterial.PDF, GeneratedMaterial.WEB]
    assert updated.materials_stale_since_revision == revision.revision_number


def test_metadata_edit_is_optimistic_and_preserves_publication(tmp_path: Path) -> None:
    service = StudentContentService(tmp_path / "data")
    original = service.create_lesson(make_published_lesson())
    stale_timestamp = original.updated_at
    stale_row_version = service.get_lesson(original.lesson_id).row_version

    updated = service.update_lesson_metadata(
        original.lesson_id,
        student=original.student,
        subject="physics",
        lesson_date=date(2026, 7, 19),
        topic="Новая тема",
        expected_updated_at=stale_timestamp,
        expected_row_version=stale_row_version,
    )
    with pytest.raises(LessonEditConflictError):
        service.update_lesson_metadata(
            original.lesson_id,
            student=original.student,
            subject="chemistry",
            lesson_date=date(2026, 7, 20),
            topic="Конкурирующая тема",
            expected_updated_at=stale_timestamp,
            expected_row_version=stale_row_version,
        )

    persisted = service.get_lesson(original.lesson_id).lesson
    assert (persisted.subject, persisted.topic, persisted.lesson_date) == (
        "physics",
        "Новая тема",
        date(2026, 7, 19),
    )
    assert persisted.publication == original.publication
    assert persisted.latex == original.latex
    assert persisted.stale_materials == [GeneratedMaterial.PDF, GeneratedMaterial.WEB]
    assert updated.updated_at == persisted.updated_at
