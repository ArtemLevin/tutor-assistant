from __future__ import annotations

import inspect

from tutor_assistant.config import LatexConfig
from tutor_assistant.latex.remote import RemoteLatexService


def test_remote_latex_defaults_to_local_only() -> None:
    config = LatexConfig()

    assert config.auto_monitor is False
    assert config.publish_pdf is False


def test_transcript_file_path_resolves_to_lesson_root() -> None:
    root = RemoteLatexService._repository_lesson_root(
        "students/test/lessons/lesson-id/transcript.txt"
    )

    assert root.as_posix() == "students/test/lessons/lesson-id"


def test_legacy_directory_path_remains_supported() -> None:
    root = RemoteLatexService._repository_lesson_root(
        "students/test/lessons/legacy-lesson"
    )

    assert root.as_posix() == "students/test/lessons/legacy-lesson"


def test_remote_latex_compile_method_contains_no_git_push() -> None:
    source = inspect.getsource(RemoteLatexService._compile_with_probe)

    assert '"push"' not in source
    assert "_cache_result" in source
    assert "probe.remote_head" in source
