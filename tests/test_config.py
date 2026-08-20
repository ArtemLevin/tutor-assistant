from pathlib import Path

import pytest
from pydantic import ValidationError

from tutor_assistant.config import (
    AppConfig,
    ContentConfig,
    NormalizationConfig,
    RepositoryConfig,
    WhisperConfig,
)


def test_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "app.yaml"
    config = AppConfig(setup_completed=True)
    config.recording.queue_blocks = 512
    config.recording.system_device_id = "{g733-device-id}"
    config.recording.system_backend = "soundcard"
    config.recording.silence_warning_seconds = 30
    config.repository.auto_create_pr = True
    config.whisper.cpu_threads = 3
    config.content.trash_retention_days = 45
    config.content.maintenance_interval_minutes = 15
    config.content.temporary_retention_hours = 12
    config.save(path)
    restored = AppConfig.load(path)
    assert restored.setup_completed
    assert restored.recording.queue_blocks == 512
    assert restored.recording.system_device_id == "{g733-device-id}"
    assert restored.recording.system_backend == "soundcard"
    assert restored.recording.silence_warning_seconds == 30
    assert restored.repository.auto_create_pr
    assert restored.whisper.cpu_threads == 3
    assert restored.content.trash_retention_days == 45
    assert restored.content.maintenance_interval_minutes == 15
    assert restored.content.temporary_retention_hours == 12
    assert not path.with_suffix(".yaml.tmp").exists()


def test_legacy_loopback_config_remains_valid(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text(
        "recording:\n  mic_device: 22\n  loopback_device: 31\n",
        encoding="utf-8",
    )

    restored = AppConfig.load(path)

    assert restored.recording.mic_device == 22
    assert restored.recording.loopback_device == 31
    assert restored.recording.system_device_id is None


@pytest.mark.parametrize(
    ("configuration", "field", "minimum", "maximum"),
    [
        (WhisperConfig, "cpu_threads", 1, 32),
        (WhisperConfig, "num_workers", 1, 4),
        (WhisperConfig, "gigaam_chunk_seconds", 5, 24),
        (RepositoryConfig, "github_api_timeout_seconds", 1, 300),
        (ContentConfig, "trash_retention_days", 0, 3650),
        (ContentConfig, "backup_retention_count", 1, 365),
        (ContentConfig, "maintenance_interval_minutes", 5, 1440),
        (NormalizationConfig, "retry_requests", 0, 3),
        (NormalizationConfig, "retry_backoff_seconds", 0, 60),
        (NormalizationConfig, "temperature", 0, 2),
    ],
)
def test_configuration_numeric_boundaries_reject_out_of_range_values(
    configuration,
    field,
    minimum,
    maximum,
) -> None:
    for valid in (minimum, minimum + 1, maximum - 1, maximum):
        assert getattr(configuration(**{field: valid}), field) == valid

    for invalid in (minimum - 1, maximum + 1):
        with pytest.raises(ValidationError, match=field):
            configuration(**{field: invalid})


@pytest.mark.parametrize(
    ("max_segments", "overlap", "valid"),
    [(1, 0, True), (1, 1, False), (5, 3, True), (5, 4, True), (5, 5, False), (5, -1, False)],
)
def test_normalization_overlap_must_be_strictly_below_chunk_size(
    max_segments,
    overlap,
    valid,
) -> None:
    options = {"max_segments_per_chunk": max_segments, "context_overlap_segments": overlap}

    if valid:
        config = NormalizationConfig(**options)
        assert config.context_overlap_segments == overlap
    else:
        with pytest.raises(ValidationError, match="context_overlap_segments"):
            NormalizationConfig(**options)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com",
        "http://192.168.1.15:11434",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1:11434?token=secret",
        "http://user:password@127.0.0.1:11434",
        "file:///tmp/provider",
        "",
    ],
)
def test_normalization_rejects_remote_or_unsafe_ollama_endpoint(endpoint) -> None:
    with pytest.raises(ValidationError):
        NormalizationConfig(base_url=endpoint)


@pytest.mark.parametrize(
    "endpoint", ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"]
)
def test_normalization_accepts_only_explicit_loopback_endpoints_by_default(endpoint) -> None:
    assert NormalizationConfig(base_url=endpoint).base_url == endpoint


def test_remote_ollama_endpoint_requires_explicit_opt_in() -> None:
    config = NormalizationConfig(
        base_url="https://trusted-provider.example",
        allow_remote_endpoint=True,
    )

    assert config.allow_remote_endpoint
    assert config.base_url == "https://trusted-provider.example"


@pytest.mark.parametrize("variable", ["", "123TOKEN", "TOKEN-NAME", "TOKEN VALUE", "token;secret"])
def test_credential_environment_variable_name_rejects_unsafe_values(variable) -> None:
    with pytest.raises(ValidationError, match="github_token_env"):
        RepositoryConfig(github_token_env=variable)
    with pytest.raises(ValidationError, match="yandex_api_key_env"):
        NormalizationConfig(yandex_api_key_env=variable)


@pytest.mark.parametrize(
    ("legacy_attempts", "expected_retries"),
    [(0, 0), (1, 0), (2, 1), (4, 3), (100, 3)],
)
def test_legacy_normalization_attempts_migrate_without_exceeding_retry_policy(
    legacy_attempts,
    expected_retries,
) -> None:
    config = NormalizationConfig.model_validate({"max_attempts": legacy_attempts})

    assert config.retry_requests == expected_retries
    assert config.max_attempts == expected_retries + 1


@pytest.mark.parametrize(
    "options",
    [
        {"allow_cloud_processing": False, "yandex_folder_id": "folder"},
        {"allow_cloud_processing": True, "cloud_policy": "disabled", "yandex_folder_id": "folder"},
        {"allow_cloud_processing": True, "yandex_folder_id": ""},
        {"allow_cloud_processing": True, "yandex_folder_id": "   "},
    ],
)
def test_cloud_normalization_requires_explicit_consent_policy_and_folder(options) -> None:
    with pytest.raises(ValidationError):
        NormalizationConfig(provider="yandex_ai_studio", **options)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://ai.api.cloud.yandex.net/v1",
        "https://example.com/v1",
        "https://ai.api.cloud.yandex.net/v2",
        "https://ai.api.cloud.yandex.net/v1?token=secret",
        "https://secret@ai.api.cloud.yandex.net/v1",
    ],
)
def test_cloud_normalization_rejects_untrusted_or_credentialed_endpoint(endpoint) -> None:
    with pytest.raises(ValidationError, match="yandex_base_url"):
        NormalizationConfig(yandex_base_url=endpoint)


def test_missing_configuration_file_uses_safe_cloud_defaults(tmp_path: Path) -> None:
    config = AppConfig.load(tmp_path / "missing.yaml")

    assert config.normalization.effective_cloud_policy == "disabled"
    assert config.normalization.allow_cloud_processing is False
    assert config.normalization.require_manual_approval is True
