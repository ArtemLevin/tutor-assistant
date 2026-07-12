from pathlib import Path

from tutor_assistant.config import AppConfig


def test_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "app.yaml"
    config = AppConfig(setup_completed=True)
    config.recording.queue_blocks = 512
    config.recording.system_device_id = "{g733-device-id}"
    config.recording.system_backend = "soundcard"
    config.recording.silence_warning_seconds = 30
    config.repository.auto_create_pr = True
    config.save(path)
    restored = AppConfig.load(path)
    assert restored.setup_completed
    assert restored.recording.queue_blocks == 512
    assert restored.recording.system_device_id == "{g733-device-id}"
    assert restored.recording.system_backend == "soundcard"
    assert restored.recording.silence_warning_seconds == 30
    assert restored.repository.auto_create_pr


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
