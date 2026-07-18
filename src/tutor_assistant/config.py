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
    system_device_id: str | None = None
    system_backend: str = "soundcard"
    chunk_seconds: int = 30
    diagnostics_seconds: int = 5
    queue_blocks: int = 256
    target_sample_rate: int = 48_000
    dual_channel_transcription: bool = True
    require_preflight: bool = True
    silence_warning_seconds: int = 20
    device_timeout_seconds: int = 5


class WhisperConfig(BaseModel):
    model: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "ru"
    beam_size: int = 1
    vad_filter: bool = True
    cpu_threads: int = Field(default=2, ge=1, le=32)
    num_workers: int = Field(default=1, ge=1, le=4)


class RepositoryConfig(BaseModel):
    students_repo: Path = Path("../students-26-27")
    remote: str = "origin"
    base_branch: str = "main"
    push: bool = True
    create_branch: bool = True
    use_worktree: bool = True
    keep_worktree: bool = False
    auto_create_pr: bool = True
    repository_full_name: str = "ArtemLevin/students-26-27"
    pr_base_branch: str = "main"


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


class LaunchProfile(BaseModel):
    id: str = "online_lesson"
    name: str = "Обычный онлайн-урок"
    subject: str = "mathematics"
    student_id: str | None = None
    auto_transcribe: bool = True
    countdown_seconds: int = 3


class QuickStartConfig(BaseModel):
    start_in_quick_mode: bool = True
    default_profile_id: str = "online_lesson"
    last_student_id: str | None = None
    last_subject: str = "mathematics"
    last_topic: str = ""
    profiles: list[LaunchProfile] = Field(default_factory=lambda: [LaunchProfile()])


class ContentConfig(BaseModel):
    trash_retention_days: int = Field(default=30, ge=0, le=3650)
    maintenance_enabled: bool = True
    maintenance_interval_minutes: int = Field(default=30, ge=5, le=1440)
    auto_repair: bool = True
    auto_purge_trash: bool = True
    auto_cleanup_temporary: bool = True
    temporary_retention_hours: int = Field(default=24, ge=1, le=8760)
    backup_enabled: bool = True
    backup_interval_hours: int = Field(default=24, ge=1, le=8760)
    backup_retention_count: int = Field(default=14, ge=1, le=365)


class AppConfig(BaseModel):
    setup_completed: bool = False
    workspace: Path = Path("data")
    students_file: Path = Path("config/students.yaml")
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    repository: RepositoryConfig = Field(default_factory=RepositoryConfig)
    latex: LatexConfig = Field(default_factory=LatexConfig)
    quick_start: QuickStartConfig = Field(default_factory=QuickStartConfig)
    content: ContentConfig = Field(default_factory=ContentConfig)

    @classmethod
    def load(cls, path: Path) -> AppConfig:
        if not path.exists():
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            yaml.safe_dump(self.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        temporary.replace(path)


def load_students(path: Path) -> list[Student]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Student.model_validate(item) for item in data.get("students", [])]
