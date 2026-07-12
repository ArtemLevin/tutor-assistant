from datetime import date

from tutor_assistant.domain import Lesson, Student
from tutor_assistant.store import LessonStore


def test_store_upserts_lesson(tmp_path) -> None:
    store = LessonStore(tmp_path / "lessons.sqlite3")
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="physics", lesson_date=date(2026, 7, 12), topic="Резонанс",
    )
    store.save(lesson)
    lesson.topic = "Механический резонанс"
    store.save(lesson)
    assert store.get(lesson.lesson_id).topic == "Механический резонанс"
    assert len(store.list()) == 1

