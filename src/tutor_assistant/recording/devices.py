from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    max_input_channels: int
    default_sample_rate: int
    host_api: str

    @property
    def likely_loopback(self) -> bool:
        name = self.name.lower()
        return "loopback" in name or "стерео микшер" in name or "stereo mix" in name

    @property
    def is_wasapi(self) -> bool:
        return "wasapi" in self.host_api.casefold()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SystemAudioSource:
    device_id: str
    name: str
    backend: str
    channels: int
    default_sample_rate: int
    is_default: bool = False
    legacy_index: int | None = None

    @property
    def key(self) -> str:
        return f"{self.backend}:{self.device_id}"

    @property
    def display_name(self) -> str:
        suffix = " · используется Windows" if self.is_default else ""
        backend = "WASAPI LOOPBACK" if self.backend == "soundcard" else "входной fallback"
        return f"{self.name} [{backend}]{suffix}"

    def to_dict(self) -> dict[str, object]:
        return asdict(self) | {"key": self.key}


def list_input_devices() -> list[AudioDevice]:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Установите sounddevice для работы со звуком") from exc
    host_apis = sd.query_hostapis()
    result: list[AudioDevice] = []
    for index, raw in enumerate(sd.query_devices()):
        if int(raw["max_input_channels"]) < 1:
            continue
        result.append(
            AudioDevice(
                index=index,
                name=str(raw["name"]),
                max_input_channels=int(raw["max_input_channels"]),
                default_sample_rate=int(raw["default_samplerate"]),
                host_api=str(host_apis[int(raw["hostapi"])]["name"]),
            )
        )
    return result


def resolve_input_device(
    devices: list[AudioDevice],
    *,
    device_index: int | None = None,
    device_name: str | None = None,
    host_api: str | None = None,
    prefer_wasapi: bool = True,
) -> AudioDevice | None:
    """Resolve a live microphone without treating the PortAudio index as persistent identity."""

    if not devices:
        return None

    normalized_name = (device_name or "").strip().casefold()
    normalized_host = (host_api or "").strip().casefold()

    if normalized_name:
        named = [device for device in devices if device.name.strip().casefold() == normalized_name]
        if not named:
            return None
        exact_host = [
            device for device in named if device.host_api.strip().casefold() == normalized_host
        ]
        if prefer_wasapi:
            wasapi = [device for device in named if device.is_wasapi]
            if wasapi and (not exact_host or not exact_host[0].is_wasapi):
                return min(wasapi, key=lambda device: device.index)
        if exact_host:
            return min(exact_host, key=lambda device: device.index)
        wasapi = [device for device in named if device.is_wasapi]
        if wasapi:
            return min(wasapi, key=lambda device: device.index)
        return min(named, key=lambda device: device.index)

    indexed = next((device for device in devices if device.index == device_index), None)
    if indexed is None:
        return None
    if prefer_wasapi and not indexed.is_wasapi:
        same_name_wasapi = [
            device
            for device in devices
            if device.name.strip().casefold() == indexed.name.strip().casefold() and device.is_wasapi
        ]
        if same_name_wasapi:
            return min(same_name_wasapi, key=lambda device: device.index)
    return indexed


def probe_input_device(device: AudioDevice, *, channels: int = 1) -> None:
    """Open and close the live endpoint so stale Windows/PortAudio IDs fail before lesson creation."""

    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Установите sounddevice для работы со звуком") from exc

    stream = None
    try:
        stream = sd.InputStream(
            device=device.index,
            samplerate=device.default_sample_rate,
            channels=channels,
            dtype="float32",
        )
    except Exception as exc:
        raise RuntimeError(
            f"Микрофон «{device.name}» [{device.host_api}] не удалось открыть. "
            "Windows могла перенумеровать или отключить аудиоустройство. "
            "Переподключите микрофон и выберите его заново; на Windows рекомендуется WASAPI."
        ) from exc
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


def _channel_count(channels: object) -> int:
    if isinstance(channels, int):
        return max(1, channels)
    try:
        return max(1, len(channels))  # type: ignore[arg-type]
    except TypeError:
        return 2


def list_loopback_devices(sample_rate: int = 48_000) -> list[SystemAudioSource]:
    try:
        import soundcard as sc
    except Exception as exc:
        raise RuntimeError(f"SoundCard недоступен: {exc}") from exc

    try:
        regular_ids = {str(device.id) for device in sc.all_microphones(include_loopback=False)}
        default_speaker = sc.default_speaker()
        default_id = str(default_speaker.id) if default_speaker else None
        sources = []
        for device in sc.all_microphones(include_loopback=True):
            device_id = str(device.id)
            if device_id in regular_ids:
                continue
            sources.append(
                SystemAudioSource(
                    device_id=device_id,
                    name=str(device.name),
                    backend="soundcard",
                    channels=_channel_count(device.channels),
                    default_sample_rate=sample_rate,
                    is_default=device_id == default_id,
                )
            )
        return sorted(sources, key=lambda item: (not item.is_default, item.name.casefold()))
    except Exception as exc:
        raise RuntimeError(f"Не удалось получить WASAPI Loopback-устройства: {exc}") from exc


def list_system_audio_sources(
    input_devices: list[AudioDevice] | None = None, sample_rate: int = 48_000
) -> list[SystemAudioSource]:
    try:
        sources = list_loopback_devices(sample_rate)
    except Exception:
        sources = []
    inputs = input_devices if input_devices is not None else list_input_devices()
    known = {source.name.casefold() for source in sources}
    for device in inputs:
        if not device.likely_loopback or device.name.casefold() in known:
            continue
        sources.append(
            SystemAudioSource(
                device_id=str(device.index),
                name=device.name,
                backend="sounddevice",
                channels=device.max_input_channels,
                default_sample_rate=device.default_sample_rate,
                legacy_index=device.index,
            )
        )
    return sources
