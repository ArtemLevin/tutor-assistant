from tutor_assistant.config import AppConfig
from tutor_assistant.domain import Student
from tutor_assistant.quick_start import evaluate_readiness, selected_profile
from tutor_assistant.recording import AudioDevice, SystemAudioSource


def test_quick_start_ready_with_saved_devices() -> None:
    config = AppConfig()
    config.recording.mic_device = 22
    config.recording.system_device_id = "g733"
    students = [Student(id="timofey", full_name="Тимофей")]
    devices = [AudioDevice(22, "Fifine", 2, 48_000, "Windows WASAPI")]
    sources = [SystemAudioSource("g733", "G733", "soundcard", 2, 48_000)]

    result = evaluate_readiness(config, students, devices, sources, "timofey", "Производная")

    assert result.ready
    assert not result.blockers


def test_quick_start_reports_every_blocker() -> None:
    result = evaluate_readiness(AppConfig(), [], [], [], None, "")

    assert not result.ready
    assert {item.code for item in result.blockers} == {
        "student",
        "topic",
        "microphone",
        "system",
    }


def test_default_profile_is_resolved() -> None:
    profile = selected_profile(AppConfig())

    assert profile.id == "online_lesson"
    assert profile.auto_transcribe
