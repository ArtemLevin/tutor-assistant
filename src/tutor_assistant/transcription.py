from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from .config import WhisperConfig


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


@dataclass
class Segment:
    start: float
    end: float
    text: str
    avg_logprob: float | None
    no_speech_prob: float | None
    speaker: str | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    output_dir: Path
    raw: Path
    timestamped: Path
    cleaned: Path
    segments: Path
    signals: Path
    manifest: Path
    teacher_transcript: Path | None = None
    student_transcript: Path | None = None


SIGNALS = [
    "не понимаю",
    "не понял",
    "не поняла",
    "можно ещё раз",
    "можно еще раз",
    "не получается",
    "другой ответ",
    "не сходится",
    "я запутался",
    "я запуталась",
]


def clean_transcript(text: str) -> str:
    text = text.replace("\ufeff", " ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?:\b(?:угу|ага|да|так|ну)\b[,.!?\s]*){5,}", " ", text, flags=re.I)
    organizational = [
        r"\b(?:меня )?слышно\b[^.!?]{0,80}[.!?]?",
        r"\b(?:видно|видите) (?:экран|демонстрацию|доску)?\b[^.!?]{0,80}[.!?]?",
        r"\b(?:здравствуйте|добрый день|секундочку)\b[.!?]?",
    ]
    for pattern in organizational:
        text = re.sub(pattern, " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def extract_signals(text: str, speaker: str | None = None) -> list[dict[str, str | int]]:
    found: list[dict[str, str | int]] = []
    lowered = text.lower()
    for signal in SIGNALS:
        start = 0
        while (position := lowered.find(signal, start)) >= 0:
            item: dict[str, str | int] = {
                "signal": signal,
                "position": position,
                "snippet": text[max(0, position - 100) : position + len(signal) + 100],
            }
            if speaker:
                item["speaker"] = speaker
            found.append(item)
            start = position + len(signal)
    return found


class _BaseTranscriber:
    provider_name = "unknown"
    model_name = "unknown"
    raw_filename = "00_raw_transcript.txt"

    def _recognize(
        self,
        audio: Path,
        *,
        speaker: str | None = None,
        offset_seconds: float = 0.0,
    ) -> tuple[list[Segment], dict]:
        raise NotImplementedError

    def transcribe(self, audio: Path, output_dir: Path) -> TranscriptionResult:
        started = perf_counter()
        segments, source = self._recognize(audio)
        return self._write_result(output_dir, segments, [source], started)

    def transcribe_dual(
        self,
        microphone: Path,
        system: Path,
        output_dir: Path,
        *,
        microphone_offset_seconds: float = 0.0,
        system_offset_seconds: float = 0.0,
    ) -> TranscriptionResult:
        started = perf_counter()
        teacher, teacher_source = self._recognize(
            microphone,
            speaker="П",
            offset_seconds=microphone_offset_seconds,
        )
        student, student_source = self._recognize(
            system,
            speaker="У",
            offset_seconds=system_offset_seconds,
        )
        merged = sorted([*teacher, *student], key=lambda item: (item.start, item.end))
        output_dir.mkdir(parents=True, exist_ok=True)
        teacher_text = output_dir / "teacher_transcript.txt"
        student_text = output_dir / "student_transcript.txt"
        teacher_json = output_dir / "teacher_segments.json"
        student_json = output_dir / "student_segments.json"
        _atomic_write_text(teacher_text, " ".join(item.text for item in teacher))
        _atomic_write_text(student_text, " ".join(item.text for item in student))
        _atomic_write_text(
            teacher_json,
            json.dumps([asdict(item) for item in teacher], ensure_ascii=False, indent=2),
        )
        _atomic_write_text(
            student_json,
            json.dumps([asdict(item) for item in student], ensure_ascii=False, indent=2),
        )
        return self._write_result(
            output_dir,
            merged,
            [teacher_source, student_source],
            started,
            teacher_transcript=teacher_text,
            student_transcript=student_text,
            student_segments=student,
        )

    def _write_result(
        self,
        output_dir: Path,
        segments: list[Segment],
        sources: list[dict],
        started: float,
        *,
        teacher_transcript: Path | None = None,
        student_transcript: Path | None = None,
        student_segments: list[Segment] | None = None,
    ) -> TranscriptionResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        dual = any(item.speaker for item in segments)
        raw_text = " ".join(
            f"[{item.speaker}] {item.text}" if item.speaker else item.text for item in segments
        )
        cleaned_text = clean_transcript(raw_text)
        raw = output_dir / self.raw_filename
        timestamped = output_dir / "00_raw_timestamped.txt"
        cleaned = output_dir / "03_content_only_medium.txt"
        segments_file = output_dir / "00_raw_segments.json"
        signals = output_dir / "important_student_signals.json"
        manifest = output_dir / "manifest.json"
        _atomic_write_text(raw, raw_text)
        _atomic_write_text(
            timestamped,
            "\n".join(
                f"[{item.start:08.2f} — {item.end:08.2f}] "
                f"{f'[{item.speaker}] ' if item.speaker else ''}{item.text}"
                for item in segments
            ),
        )
        _atomic_write_text(cleaned, cleaned_text)
        _atomic_write_text(
            segments_file,
            json.dumps([asdict(item) for item in segments], ensure_ascii=False, indent=2),
        )
        signal_source = (
            " ".join(item.text for item in student_segments)
            if student_segments is not None
            else raw_text
        )
        _atomic_write_text(
            signals,
            json.dumps(
                extract_signals(
                    signal_source,
                    "У" if student_segments is not None else None,
                ),
                ensure_ascii=False,
                indent=2,
            ),
        )
        _atomic_write_text(
            manifest,
            json.dumps(
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "provider": self.provider_name,
                    "model": self.model_name,
                    "dual_channel": dual,
                    "sources": sources,
                    "elapsed_seconds": round(perf_counter() - started, 3),
                    "segment_count": len(segments),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        return TranscriptionResult(
            output_dir,
            raw,
            timestamped,
            cleaned,
            segments_file,
            signals,
            manifest,
            teacher_transcript,
            student_transcript,
        )


class GigaAMTranscriber(_BaseTranscriber):
    provider_name = "gigaam"
    raw_filename = "00_raw_gigaam.txt"

    def __init__(self, config: WhisperConfig) -> None:
        self.config = config
        self._model = None

    @property
    def model_name(self) -> str:
        return self.config.gigaam_model

    def _load(self):
        if self._model is None:
            try:
                import gigaam
            except ImportError as exc:
                raise RuntimeError(
                    "GigaAM не установлен. Установите актуальный пакет из официального "
                    "репозитория salute-developers/GigaAM вместе с PyTorch/Torchaudio."
                ) from exc
            device = None if self.config.gigaam_device == "auto" else self.config.gigaam_device
            try:
                self._model = gigaam.load_model(
                    self.config.gigaam_model,
                    device=device,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Не удалось загрузить модель GigaAM «{self.config.gigaam_model}». "
                    "Проверьте установку актуальной версии GigaAM, доступ к модели и "
                    "совместимость PyTorch с выбранным устройством."
                ) from exc
        return self._model

    def _segment_audio(self, audio: Path, directory: Path) -> list[Path]:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("Для GigaAM требуется ffmpeg в PATH")
        pattern = directory / "chunk_%05d.wav"
        command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(audio),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "segment",
            "-segment_time",
            str(self.config.gigaam_chunk_seconds),
            "-reset_timestamps",
            "1",
            str(pattern),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        chunks = sorted(directory.glob("chunk_*.wav"))
        if completed.returncode != 0 or not chunks:
            details = completed.stderr.strip() or "ffmpeg не создал аудиофрагменты"
            raise RuntimeError(f"Не удалось подготовить аудио для GigaAM: {details}")
        return chunks

    @staticmethod
    def _chunk_duration(path: Path) -> float:
        try:
            import soundfile as sf
        except ImportError as exc:
            raise RuntimeError("Установите soundfile для обработки аудио GigaAM") from exc
        return float(sf.info(str(path)).duration)

    def _recognize(
        self,
        audio: Path,
        *,
        speaker: str | None = None,
        offset_seconds: float = 0.0,
    ) -> tuple[list[Segment], dict]:
        model = self._load()
        segments: list[Segment] = []
        chunk_metadata: list[dict[str, float | str | int]] = []
        source_offset = 0.0
        with tempfile.TemporaryDirectory(prefix="tutor-gigaam-") as temporary:
            chunks = self._segment_audio(audio, Path(temporary))
            for index, chunk in enumerate(chunks):
                duration = self._chunk_duration(chunk)
                try:
                    result = model.transcribe(str(chunk), word_timestamps=True)
                except TypeError:
                    result = model.transcribe(str(chunk))
                text = str(getattr(result, "text", result)).strip()
                words = getattr(result, "words", None) or []
                if text:
                    if words:
                        start = source_offset + float(words[0].start)
                        end = source_offset + float(words[-1].end)
                    else:
                        start = source_offset
                        end = source_offset + duration
                    segments.append(
                        Segment(
                            start + offset_seconds,
                            end + offset_seconds,
                            text,
                            None,
                            None,
                            speaker,
                        )
                    )
                chunk_metadata.append(
                    {
                        "index": index,
                        "start_seconds": round(source_offset, 3),
                        "duration_seconds": round(duration, 3),
                    }
                )
                source_offset += duration
        return segments, {
            "source_audio": str(audio),
            "provider": self.provider_name,
            "model": self.model_name,
            "language": "ru",
            "duration_seconds": round(source_offset, 3),
            "speaker": speaker,
            "offset_seconds": offset_seconds,
            "chunk_seconds": self.config.gigaam_chunk_seconds,
            "chunks": chunk_metadata,
        }


class WhisperTranscriber(_BaseTranscriber):
    """Backward-compatible facade for the selected local ASR provider."""

    provider_name = "faster_whisper"
    raw_filename = "00_raw_whisper.txt"

    def __init__(self, config: WhisperConfig) -> None:
        self.config = config
        self._model = None
        self._gigaam: GigaAMTranscriber | None = None
        self._gigaam_signature: tuple[str, str, float] | None = None

    @property
    def model_name(self) -> str:
        return self.config.model

    def _selected_gigaam(self) -> GigaAMTranscriber:
        signature = (
            self.config.gigaam_model,
            self.config.gigaam_device,
            self.config.gigaam_chunk_seconds,
        )
        if self._gigaam is None or signature != self._gigaam_signature:
            self._gigaam = GigaAMTranscriber(self.config)
            self._gigaam_signature = signature
        return self._gigaam

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError("Установите tutor-assistant[transcription]") from exc
            self._model = WhisperModel(
                self.config.model,
                device=self.config.device,
                compute_type=self.config.compute_type,
                cpu_threads=self.config.cpu_threads,
                num_workers=self.config.num_workers,
            )
        return self._model

    def _recognize(
        self,
        audio: Path,
        *,
        speaker: str | None = None,
        offset_seconds: float = 0.0,
    ) -> tuple[list[Segment], dict]:
        generator, info = self._load().transcribe(
            str(audio),
            language=self.config.language,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
            temperature=0.0,
            condition_on_previous_text=False,
        )
        segments: list[Segment] = []
        for item in generator:
            text = str(item.text).strip()
            if text:
                segments.append(
                    Segment(
                        float(item.start) + offset_seconds,
                        float(item.end) + offset_seconds,
                        text,
                        float(item.avg_logprob) if item.avg_logprob is not None else None,
                        float(item.no_speech_prob) if item.no_speech_prob is not None else None,
                        speaker,
                    )
                )
        return segments, {
            "source_audio": str(audio),
            "provider": self.provider_name,
            "language": getattr(info, "language", self.config.language),
            "duration_seconds": getattr(info, "duration", None),
            "speaker": speaker,
            "offset_seconds": offset_seconds,
        }

    def transcribe(self, audio: Path, output_dir: Path) -> TranscriptionResult:
        if self.config.provider == "gigaam":
            return self._selected_gigaam().transcribe(audio, output_dir)
        return super().transcribe(audio, output_dir)

    def transcribe_dual(
        self,
        microphone: Path,
        system: Path,
        output_dir: Path,
        *,
        microphone_offset_seconds: float = 0.0,
        system_offset_seconds: float = 0.0,
    ) -> TranscriptionResult:
        if self.config.provider == "gigaam":
            return self._selected_gigaam().transcribe_dual(
                microphone,
                system,
                output_dir,
                microphone_offset_seconds=microphone_offset_seconds,
                system_offset_seconds=system_offset_seconds,
            )
        return super().transcribe_dual(
            microphone,
            system,
            output_dir,
            microphone_offset_seconds=microphone_offset_seconds,
            system_offset_seconds=system_offset_seconds,
        )
