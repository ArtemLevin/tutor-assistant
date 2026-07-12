from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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


@dataclass(frozen=True)
class TranscriptionResult:
    output_dir: Path
    raw: Path
    timestamped: Path
    cleaned: Path
    segments: Path
    signals: Path
    manifest: Path


SIGNALS = [
    "не понимаю", "не понял", "не поняла", "можно ещё раз", "можно еще раз",
    "не получается", "другой ответ", "не сходится", "я запутался", "я запуталась",
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


def extract_signals(text: str) -> list[dict[str, str | int]]:
    found: list[dict[str, str | int]] = []
    lowered = text.lower()
    for signal in SIGNALS:
        start = 0
        while (position := lowered.find(signal, start)) >= 0:
            found.append({
                "signal": signal,
                "position": position,
                "snippet": text[max(0, position - 100): position + len(signal) + 100],
            })
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

    def transcribe(self, audio: Path, output_dir: Path) -> TranscriptionResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        started = perf_counter()
        generator, info = self._load().transcribe(
            str(audio), language=self.config.language, beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter, temperature=0.0, condition_on_previous_text=False,
        )
        segments: list[Segment] = []
        for item in generator:
            text = str(item.text).strip()
            if text:
                segments.append(Segment(
                    float(item.start), float(item.end), text,
                    float(item.avg_logprob) if item.avg_logprob is not None else None,
                    float(item.no_speech_prob) if item.no_speech_prob is not None else None,
                ))
        raw_text = " ".join(item.text for item in segments)
        cleaned_text = clean_transcript(raw_text)
        raw = output_dir / "00_raw_whisper.txt"
        timestamped = output_dir / "00_raw_timestamped.txt"
        cleaned = output_dir / "03_content_only_medium.txt"
        segments_file = output_dir / "00_raw_segments.json"
        signals = output_dir / "important_student_signals.json"
        manifest = output_dir / "manifest.json"
        raw.write_text(raw_text, encoding="utf-8")
        timestamped.write_text("\n".join(
            f"[{s.start:08.2f} — {s.end:08.2f}] {s.text}" for s in segments
        ), encoding="utf-8")
        cleaned.write_text(cleaned_text, encoding="utf-8")
        segments_file.write_text(json.dumps([asdict(s) for s in segments], ensure_ascii=False, indent=2), encoding="utf-8")
        signals.write_text(json.dumps(extract_signals(raw_text), ensure_ascii=False, indent=2), encoding="utf-8")
        manifest.write_text(json.dumps({
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_audio": str(audio),
            "model": self.config.model,
            "language": getattr(info, "language", self.config.language),
            "duration_seconds": getattr(info, "duration", None),
            "elapsed_seconds": round(perf_counter() - started, 3),
            "segment_count": len(segments),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return TranscriptionResult(output_dir, raw, timestamped, cleaned, segments_file, signals, manifest)

