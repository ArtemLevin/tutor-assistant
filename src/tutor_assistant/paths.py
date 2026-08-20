"""Keep installed application binaries physically separate from user data."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APPLICATION_NAME = "TutorAssistant"
PORTABLE_MARKER = "portable.mode"


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    mode: str
    application_directory: Path
    configuration_directory: Path
    workspace_directory: Path

    @property
    def configuration_file(self) -> Path:
        return self.configuration_directory / "app.yaml"

    @property
    def students_file(self) -> Path:
        return self.configuration_directory / "students.yaml"


def application_paths() -> ApplicationPaths:
    if not getattr(sys, "frozen", False):
        return ApplicationPaths("source", Path.cwd(), Path("config"), Path("data"))
    application_directory = Path(sys.executable).resolve().parent
    if (application_directory / PORTABLE_MARKER).is_file():
        return ApplicationPaths(
            "portable",
            application_directory,
            application_directory / "config",
            application_directory / "data",
        )
    return ApplicationPaths(
        "installed",
        application_directory,
        Path(user_config_dir(APPLICATION_NAME, appauthor=False, roaming=True)),
        Path(user_data_dir(APPLICATION_NAME, appauthor=False)),
    )


def default_config_path() -> Path:
    return application_paths().configuration_file


def default_workspace_path() -> Path:
    return application_paths().workspace_directory


def default_students_path() -> Path:
    return application_paths().students_file
