import json
from datetime import date, datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from tutor_assistant.config import RepositoryConfig
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.publisher import LessonPublisher


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


def test_publisher_creates_structured_job_status(tmp_path: Path) -> None:
    lesson = _lesson()

    target = LessonPublisher(RepositoryConfig())._copy_job(lesson, tmp_path)
    payload = json.loads((target / "job.status.json").read_text(encoding="utf-8"))

    assert payload["schema_version"] == "1.0"
    assert payload["lesson_id"] == lesson.lesson_id
    assert payload["status"] == "ready_for_generation"
    assert payload["stage"] == "generation"
    assert payload["artifacts"] == {
        "tex": "pending",
        "pdf": "pending",
        "poster": "pending",
        "web": "pending",
        "index": "pending",
    }
    assert datetime.fromisoformat(payload["updated_at"]).tzinfo is not None
    assert not list(
        Draft202012Validator(_schema(), format_checker=FormatChecker()).iter_errors(payload)
    )


def test_job_status_schema_accepts_legacy_marker() -> None:
    legacy = {"status": "ready_for_generation"}

    assert not list(
        Draft202012Validator(_schema(), format_checker=FormatChecker()).iter_errors(legacy)
    )
