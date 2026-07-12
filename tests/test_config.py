from pathlib import Path

from tutor_assistant.config import AppConfig


def test_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "app.yaml"
    config = AppConfig(setup_completed=True)
    config.recording.queue_blocks = 512
    config.repository.auto_create_pr = True
    config.save(path)
    restored = AppConfig.load(path)
    assert restored.setup_completed
    assert restored.recording.queue_blocks == 512
    assert restored.repository.auto_create_pr
