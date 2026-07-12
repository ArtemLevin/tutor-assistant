from datetime import date
from pathlib import Path

from tutor_assistant.domain import JobStatus, Lesson, Student


def test_lesson_round_trip(tmp_path: Path) -> None:
    lesson = Lesson(
        student=Student(id="test_student", full_name="Тестовый Ученик", grade=10),
        subject="mathematics",
        lesson_date=date(2026, 7, 12),
        topic="Метод интервалов",
    )
    lesson.transition(JobStatus.READY)
    path = tmp_path / "lesson.json"
    lesson.write_json(path)
    restored = Lesson.read_json(path)
    assert restored.lesson_id == lesson.lesson_id
    assert restored.status == JobStatus.READY
    assert restored.lesson_slug.startswith("2026-07-12_")


def test_student_repository_folder_override() -> None:
    student = Student(id="abc", full_name="ABC", repository_folder="custom/abc")
    assert student.folder == "custom/abc"

