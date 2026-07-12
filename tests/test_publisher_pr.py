from datetime import date
from pathlib import Path
from subprocess import CompletedProcess

from tutor_assistant.config import RepositoryConfig
from tutor_assistant.domain import Lesson, Student
from tutor_assistant.publisher import create_draft_pr


def test_create_draft_pr(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tutor_assistant.publisher.shutil.which", lambda command: "/bin/gh")

    def fake_run(command, **kwargs):
        if command[1:3] == ["auth", "status"]:
            return CompletedProcess(command, 0, "", "")
        if command[1:3] == ["pr", "view"]:
            return CompletedProcess(command, 1, "", "missing")
        return CompletedProcess(command, 0, "https://github.com/example/pull/1\n", "")

    monkeypatch.setattr("tutor_assistant.publisher.subprocess.run", fake_run)
    lesson = Lesson(
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics", lesson_date=date(2026, 7, 12), topic="Метод интервалов",
    )
    url, warnings = create_draft_pr(
        RepositoryConfig(repository_full_name="example/repo"), tmp_path, lesson, "lesson/test"
    )
    assert url == "https://github.com/example/pull/1"
    assert warnings == []
