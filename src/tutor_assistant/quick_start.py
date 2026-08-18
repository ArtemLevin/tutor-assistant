from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .application.audio_devices import (
    AudioInputDeviceSnapshot,
    SystemAudioSourceSnapshot,
    resolve_input_device_identity,
)
from .config import AppConfig, LaunchProfile
from .domain import Student


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
    devices: Sequence[AudioInputDeviceSnapshot],
    system_sources: Sequence[SystemAudioSourceSnapshot],
    student_id: str | None,
    topic: str,
) -> LaunchReadiness:
    student = next((item for item in students if item.id == student_id), None)
    microphone = resolve_input_device_identity(
        devices,
        device_index=config.recording.mic_device,
        device_name=config.recording.mic_device_name,
        host_api=config.recording.mic_host_api,
    )
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
                (
                    f"{microphone.name} [{microphone.host_api}]"
                    if microphone
                    else "Сохранённый микрофон недоступен"
                ),
            ),
            ReadinessItem(
                "system",
                "Звук ученика",
                system is not None,
                system.name if system else "Сохранённый loopback-выход недоступен",
            ),
        )
    )
