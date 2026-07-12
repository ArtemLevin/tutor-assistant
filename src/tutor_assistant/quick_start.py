from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig, LaunchProfile
from .domain import Student
from .recording import AudioDevice, SystemAudioSource


@dataclass(frozen=True)
class ReadinessItem:
    code: str
    label: str
    ready: bool
    detail: str
    critical: bool = True


@dataclass(frozen=True)
class LaunchReadiness:
    items: tuple[ReadinessItem, ...]

    @property
    def ready(self) -> bool:
        return all(item.ready for item in self.items if item.critical)

    @property
    def blockers(self) -> tuple[ReadinessItem, ...]:
        return tuple(item for item in self.items if item.critical and not item.ready)


def selected_profile(config: AppConfig, profile_id: str | None = None) -> LaunchProfile:
    profiles = config.quick_start.profiles or [LaunchProfile()]
    wanted = profile_id or config.quick_start.default_profile_id
    return next((item for item in profiles if item.id == wanted), profiles[0])


def evaluate_readiness(
    config: AppConfig,
    students: list[Student],
    devices: list[AudioDevice],
    system_sources: list[SystemAudioSource],
    student_id: str | None,
    topic: str,
) -> LaunchReadiness:
    student = next((item for item in students if item.id == student_id), None)
    microphone = next((item for item in devices if item.index == config.recording.mic_device), None)
    system = next(
        (
            item
            for item in system_sources
            if item.device_id == config.recording.system_device_id
            and item.backend == config.recording.system_backend
        ),
        None,
    )
    return LaunchReadiness(
        (
            ReadinessItem(
                "student",
                "Ученик",
                student is not None,
                student.full_name if student else "Выберите ученика",
            ),
            ReadinessItem(
                "topic",
                "Тема",
                bool(topic.strip()),
                topic.strip() or "Укажите тему занятия",
            ),
            ReadinessItem(
                "microphone",
                "Микрофон",
                microphone is not None,
                microphone.name if microphone else "Сохранённый микрофон недоступен",
            ),
            ReadinessItem(
                "system",
                "Звук ученика",
                system is not None,
                system.name if system else "Сохранённый loopback-выход недоступен",
            ),
        )
    )
