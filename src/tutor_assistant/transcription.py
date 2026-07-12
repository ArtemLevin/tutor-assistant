from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from .config import WhisperConfig


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


class WhisperTranscriber:
    def __init__(self, config: WhisperConfig) -> None:
        self.config = config
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError("Установите tutor-assistant[transcription]") from exc
            self._model = WhisperModel(
                self.config.model, device=self.config.device, compute_type=self.config.compute_type
            )
        return self._model

    def _recognize(
        self, audio: Path, *, speaker: str | None = None, offset_seconds: float = 0.0
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
            "language": getattr(info, "language", self.config.language),
            "duration_seconds": getattr(info, "duration", None),
            "speaker": speaker,
            "offset_seconds": offset_seconds,
        }

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
            microphone, speaker="Преподаватель", offset_seconds=microphone_offset_seconds
        )
        student, student_source = self._recognize(
            system, speaker="Ученик", offset_seconds=system_offset_seconds
        )
        merged = sorted([*teacher, *student], key=lambda item: (item.start, item.end))
        output_dir.mkdir(parents=True, exist_ok=True)
        teacher_text = output_dir / "teacher_transcript.txt"
        student_text = output_dir / "student_transcript.txt"
        teacher_json = output_dir / "teacher_segments.json"
        student_json = output_dir / "student_segments.json"
        teacher_text.write_text(" ".join(item.text for item in teacher), encoding="utf-8")
        student_text.write_text(" ".join(item.text for item in student), encoding="utf-8")
        teacher_json.write_text(
            json.dumps([asdict(item) for item in teacher], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        student_json.write_text(
            json.dumps([asdict(item) for item in student], ensure_ascii=False, indent=2), encoding="utf-8"
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
        raw = output_dir / "00_raw_whisper.txt"
        timestamped = output_dir / "00_raw_timestamped.txt"
        cleaned = output_dir / "03_content_only_medium.txt"
        segments_file = output_dir / "00_raw_segments.json"
        signals = output_dir / "important_student_signals.json"
        manifest = output_dir / "manifest.json"
        raw.write_text(raw_text, encoding="utf-8")
        timestamped.write_text(
            "\n".join(
                f"[{item.start:08.2f} — {item.end:08.2f}] "
                f"{f'[{item.speaker}] ' if item.speaker else ''}{item.text}"
                for item in segments
            ),
            encoding="utf-8",
        )
        cleaned.write_text(cleaned_text, encoding="utf-8")
        segments_file.write_text(
            json.dumps([asdict(item) for item in segments], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        signal_source = (
            " ".join(item.text for item in student_segments) if student_segments is not None else raw_text
        )
        signals.write_text(
            json.dumps(
                extract_signals(signal_source, "Ученик" if student_segments is not None else None),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        manifest.write_text(
            json.dumps(
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "model": self.config.model,
                    "dual_channel": dual,
                    "sources": sources,
                    "elapsed_seconds": round(perf_counter() - started, 3),
                    "segment_count": len(segments),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
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
