from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .domain import Student


class RecordingConfig(BaseModel):
    sample_rate: int = 48_000
    channels: int = 1
    subtype: str = "PCM_16"
    mic_device: int | None = None
    loopback_device: int | None = None
    chunk_seconds: int = 30
    diagnostics_seconds: int = 5


class WhisperConfig(BaseModel):
    model: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "ru"
    beam_size: int = 1
    vad_filter: bool = True


class RepositoryConfig(BaseModel):
    students_repo: Path = Path("../students-26-27")
    remote: str = "origin"
    base_branch: str = "main"
    push: bool = True
    create_branch: bool = True
    use_worktree: bool = True
    keep_worktree: bool = False


class LatexConfig(BaseModel):
    enabled: bool = True
    auto_monitor: bool = True
    engine: str = "pdflatex"
    latexmk_command: str = "latexmk"
    timeout_seconds: int = 180
    keep_build_files: bool = False
    publish_pdf: bool = True
    max_attempts: int = 2
    render_preview: bool = True
    preview_dpi: int = 120
    poll_seconds: int = 60


class AppConfig(BaseModel):
    workspace: Path = Path("data")
    students_file: Path = Path("config/students.yaml")
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    repository: RepositoryConfig = Field(default_factory=RepositoryConfig)
    latex: LatexConfig = Field(default_factory=LatexConfig)

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        if not path.exists():
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def load_students(path: Path) -> list[Student]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Student.model_validate(item) for item in data.get("students", [])]
