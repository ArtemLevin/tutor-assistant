from __future__ import annotations

from pathlib import PurePosixPath

from .domain import Lesson


def automatic_publication_repository_path(lesson: Lesson) -> PurePosixPath:
    """Return the immutable automatic-publication target for a lesson."""

    student_root = PurePosixPath(lesson.student.folder)
    if student_root.is_absolute() or ".." in student_root.parts:
        raise ValueError("Путь ученика небезопасен для automatic publication")
    return student_root / "transcript" / f"{lesson.lesson_date:%d.%m.%y}.txt"
