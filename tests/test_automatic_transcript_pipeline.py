from __future__ import annotations

from datetime import date

from tutor_assistant.automatic_publication import automatic_publication_repository_path
from tutor_assistant.domain import JobStatus, Lesson, LessonProcessingMode, Student
from tutor_assistant.store import LessonStore


def _lesson() -> Lesson:
    return Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics",
        lesson_date=date(2026, 10, 4),
        topic="Волны",
    )


def test_legacy_lesson_defaults_to_manual_processing_mode() -> None:
    payload = _lesson().model_dump(mode="json")
    payload["pipeline"].pop("processing_mode", None)

    restored = Lesson.model_validate(payload)

    assert restored.pipeline.processing_mode == LessonProcessingMode.MANUAL


def test_automatic_processing_mode_round_trip() -> None:
    lesson = _lesson()
    lesson.pipeline.processing_mode = LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB

    restored = Lesson.model_validate_json(lesson.model_dump_json())

    assert restored.pipeline.processing_mode == LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB


def test_automatic_processing_mode_persists_across_store_restart(tmp_path) -> None:
    path = tmp_path / "lessons.sqlite3"
    lesson = _lesson()
    lesson.pipeline.processing_mode = LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB

    LessonStore(path).save(lesson)
    restored = LessonStore(path).get(lesson.lesson_id)

    assert restored is not None
    assert restored.pipeline.processing_mode == LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB


def test_automatic_publication_path_uses_lesson_date() -> None:
    lesson = _lesson()
    lesson.student.repository_folder = "students/test"

    assert (
        automatic_publication_repository_path(lesson).as_posix()
        == "students/test/transcript/04.10.26.txt"
    )



def test_review_required_can_transition_to_published_for_authorized_auto_path() -> None:
    lesson = _lesson()
    lesson.status = JobStatus.REVIEW_REQUIRED

    lesson.transition(JobStatus.PUBLISHED)

    assert lesson.status == JobStatus.PUBLISHED
