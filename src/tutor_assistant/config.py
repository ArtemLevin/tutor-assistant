from __future__ import annotations

import logging
import re
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .atomic_io import atomic_write_text
from .domain import Student


class RecordingConfig(BaseModel):
    sample_rate: int = 48_000
    channels: int = 1
    subtype: str = "PCM_16"
    output_format: Literal["m4a", "mp3", "wav"] = "m4a"
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
    repository_full_name: str = "owner/private-students-repo"
    pr_base_branch: str = "main"
    github_token_env: str = "GITHUB_TOKEN"
    github_api_timeout_seconds: float = Field(default=30, ge=1, le=300)

    @field_validator("github_token_env")
    @classmethod
    def validate_github_token_env(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("github_token_env должен быть именем переменной окружения")
        return value


class LatexConfig(BaseModel):
    enabled: bool = True
    auto_monitor: bool = False
    engine: str = "pdflatex"
    latexmk_command: str = "latexmk"
    timeout_seconds: int = 180
    keep_build_files: bool = False
    publish_pdf: bool = False
    max_attempts: int = 2
    render_preview: bool = True
    preview_dpi: int = 120
    poll_seconds: int = 60
    reservation_timeout_minutes: int = Field(default=30, ge=5, le=1440)
    remote_fetch_attempts: int = Field(default=3, ge=1, le=10)
    remote_fetch_backoff_seconds: float = Field(default=0.5, ge=0, le=30)


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
    maintenance_max_lessons_per_cycle: int = Field(default=50, ge=1, le=10_000)
    maintenance_max_seconds: int = Field(default=120, ge=10, le=3600)
    maintenance_apply_max_seconds: int = Field(default=30, ge=5, le=600)
    maintenance_full_scan_interval_hours: int = Field(default=168, ge=1, le=8760)


class NormalizationConfig(BaseModel):
    enabled: bool = True
    auto_run: bool = False
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    allow_remote_endpoint: bool = False
    model: str = "qwen3:8b"
    allow_cloud_processing: bool = False
    cloud_policy: Literal["disabled", "ask_every_time", "allow_for_session"] = "ask_every_time"
    credential_source: Literal["auto", "environment", "system_store"] = "auto"
    cloud_trust_env: bool = False
    cloud_max_response_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    yandex_base_url: str = "https://ai.api.cloud.yandex.net/v1"
    yandex_folder_id: str | None = None
    yandex_api_key_env: str = "YANDEX_AI_STUDIO_API_KEY"
    yandex_model: str = "yandexgpt-lite"
    mode: str = "filter_only"
    temperature: float = Field(default=0, ge=0, le=2)
    num_ctx: int = Field(default=8192, ge=1024)
    num_predict: int = Field(default=4096, ge=256)
    max_segments_per_chunk: int = Field(default=50, gt=0)
    max_input_characters: int = Field(default=12_000, gt=0)
    context_overlap_segments: int = Field(default=4, ge=0)
    request_timeout_seconds: int = Field(default=600, gt=0)
    retry_requests: int = Field(default=0, ge=0, le=3)
    retry_backoff_seconds: float = Field(default=2, ge=0, le=60)
    require_manual_approval: Literal[True] = True
    high_removal_threshold: float = Field(default=0.35, gt=0, lt=1)

    @model_validator(mode="before")
    @classmethod
    def migrate_retry_requests(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "max_attempts" not in value or "retry_requests" in value:
            return value
        migrated = dict(value)
        legacy_attempts = int(migrated.pop("max_attempts"))
        migrated["retry_requests"] = max(0, min(3, legacy_attempts - 1))
        return migrated

    @property
    def max_attempts(self) -> int:
        return self.retry_requests + 1

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in {"ollama", "yandex_ai_studio"}:
            raise ValueError("provider должен быть ollama или yandex_ai_studio")
        return value

    @field_validator("yandex_api_key_env")
    @classmethod
    def validate_yandex_api_key_env(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("yandex_api_key_env должен быть именем переменной окружения")
        return value

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value == "conservative":
            return "filter_only"
        if value != "filter_only":
            raise ValueError("Поддерживается только mode=filter_only")
        return value

    @model_validator(mode="after")
    def validate_endpoint_and_chunking(self) -> NormalizationConfig:
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("normalization.base_url должен быть HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("normalization.base_url не должен содержать credentials, query или fragment")
        if parsed.path not in {"", "/"}:
            raise ValueError("normalization.base_url не должен содержать путь")
        if self.provider == "ollama" and not self.allow_remote_endpoint:
            host = parsed.hostname.casefold()
            is_local = host == "localhost"
            if not is_local:
                try:
                    is_local = ip_address(host).is_loopback
                except ValueError:
                    is_local = False
            if not is_local:
                raise ValueError(
                    "Удалённый Ollama endpoint запрещён; используйте 127.0.0.1 "
                    "или включите allow_remote_endpoint"
                )
        yandex = urlsplit(self.yandex_base_url)
        if (
            yandex.scheme != "https"
            or yandex.hostname != "ai.api.cloud.yandex.net"
            or yandex.path.rstrip("/") != "/v1"
            or yandex.username
            or yandex.password
            or yandex.query
            or yandex.fragment
        ):
            raise ValueError(
                "normalization.yandex_base_url должен быть официальным https://ai.api.cloud.yandex.net/v1"
            )
        if self.provider == "yandex_ai_studio":
            if self.effective_cloud_policy == "disabled":
                raise ValueError(
                    "Для Yandex AI Studio явно включите allow_cloud_processing "
                    "и cloud_policy, отличный от disabled"
                )
            if not (self.yandex_folder_id or "").strip():
                raise ValueError("Для Yandex AI Studio укажите normalization.yandex_folder_id")
        if self.context_overlap_segments >= self.max_segments_per_chunk:
            raise ValueError("context_overlap_segments должен быть меньше max_segments_per_chunk")
        return self

    @property
    def effective_model(self) -> str:
        return self.yandex_model if self.provider == "yandex_ai_studio" else self.model

    @property
    def effective_cloud_policy(self) -> str:
        if not self.allow_cloud_processing or self.cloud_policy == "disabled":
            return "disabled"
        return self.cloud_policy


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
    normalization: NormalizationConfig = Field(default_factory=NormalizationConfig)

    @classmethod
    def load(cls, path: Path) -> AppConfig:
        if not path.exists():
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def save(self, path: Path) -> None:
        atomic_write_text(
            path,
            yaml.safe_dump(
                self.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=False,
            ),
        )


def load_students(path: Path) -> list[Student]:
    if not path.is_file():
        logging.warning(
            "Локальный файл учеников отсутствует; используется CRM без YAML-импорта: %s",
            path,
        )
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Student.model_validate(item) for item in data.get("students", [])]
