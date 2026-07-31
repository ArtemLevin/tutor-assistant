import json
from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from tutor_assistant.config import RepositoryConfig
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.publisher import LessonPublisher, publication_payload_files


def _lesson() -> Lesson:
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 13),
        topic="Производная",
    )
    lesson.transition(JobStatus.READY, force=True)
    return lesson


def _schema() -> dict:
    return json.loads(Path("schemas/job-status.schema.json").read_text(encoding="utf-8"))


def test_publisher_writes_only_transcript_without_job_status(tmp_path: Path) -> None:
    lesson = _lesson()
    publisher = LessonPublisher(RepositoryConfig())
    target = publisher._write_transcript(lesson, tmp_path, "Подтверждённый транскрипт\n")

    assert publication_payload_files(lesson) == (
        f"students/student/lessons/{lesson.lesson_slug}__"
        f"{lesson.lesson_id[:8]}/transcript.txt",
    )
    assert target.read_text(encoding="utf-8") == "Подтверждённый транскрипт\n"
    assert not list(tmp_path.rglob("job.status.json"))
    assert not list(tmp_path.rglob("lesson.json"))


def test_job_status_schema_accepts_legacy_marker() -> None:
    legacy = {"status": "ready_for_generation"}

    assert not list(
        Draft202012Validator(_schema(), format_checker=FormatChecker()).iter_errors(legacy)
    )
