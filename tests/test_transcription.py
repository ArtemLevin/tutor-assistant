import json

import pytest

from tutor_assistant.config import WhisperConfig
from tutor_assistant.transcription import (
    EmptyTranscriptionError,
    Segment,
    TranscriptionResult,
    WhisperTranscriber,
    clean_transcript,
    extract_signals,
    validate_transcription_result,
)


def test_clean_transcript_removes_organizational_noise() -> None:
    source = "Здравствуйте. Меня слышно нормально? Решим уравнение x плюс два равно пяти."
    result = clean_transcript(source)
    assert "слышно" not in result.lower()
    assert "уравнение" in result.lower()


def test_extract_student_signals() -> None:
    result = extract_signals("Я не понимаю, почему здесь меняется знак.")
    assert result
    assert result[0]["signal"] == "не понимаю"


def test_dual_transcription_merges_speakers(monkeypatch, tmp_path) -> None:
    transcriber = WhisperTranscriber(WhisperConfig())

    def recognize(audio, *, speaker=None, offset_seconds=0.0):
        text = "Объяснение" if speaker == "П" else "Я не понимаю"
        start = 1.0 if speaker == "П" else 2.0
        return [Segment(start + offset_seconds, start + offset_seconds + 0.5, text, -0.1, 0.0, speaker)], {
            "speaker": speaker,
            "source_audio": str(audio),
        }

    monkeypatch.setattr(transcriber, "_recognize", recognize)
    result = transcriber.transcribe_dual(tmp_path / "mic.wav", tmp_path / "system.wav", tmp_path / "out")
    segments = json.loads(result.segments.read_text(encoding="utf-8"))
    signals = json.loads(result.signals.read_text(encoding="utf-8"))
    assert [item["speaker"] for item in segments] == ["П", "У"]
    assert signals[0]["speaker"] == "У"
    assert result.teacher_transcript.exists()
    assert result.student_transcript.exists()


def _transcription_result(tmp_path, *, cleaned_text: str, segment_texts: list[str], duration: float):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    raw = output_dir / "00_raw_fake.txt"
    timestamped = output_dir / "00_raw_timestamped.txt"
    cleaned = output_dir / "03_content_only_medium.txt"
    segments = output_dir / "00_raw_segments.json"
    signals = output_dir / "important_student_signals.json"
    manifest = output_dir / "manifest.json"
    raw.write_text(" ".join(segment_texts), encoding="utf-8")
    timestamped.write_text("timestamped", encoding="utf-8")
    cleaned.write_text(cleaned_text, encoding="utf-8")
    segments.write_text(
        json.dumps(
            [
                {"start": index, "end": index + 0.5, "text": text}
                for index, text in enumerate(segment_texts)
            ]
        ),
        encoding="utf-8",
    )
    signals.write_text("[]", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "provider": "fake",
                "model": "fake-model",
                "segment_count": len(segment_texts),
                "sources": [
                    {
                        "source_audio": "/private/student/audio.wav",
                        "duration_seconds": duration,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return TranscriptionResult(
        output_dir=output_dir,
        raw=raw,
        timestamped=timestamped,
        cleaned=cleaned,
        segments=segments,
        signals=signals,
        manifest=manifest,
    )


def test_validation_rejects_empty_asr_with_privacy_safe_diagnostics(tmp_path) -> None:
    result = _transcription_result(
        tmp_path,
        cleaned_text="",
        segment_texts=[],
        duration=184.2,
    )
    quality = tmp_path / "audio_quality_report.json"
    quality.write_text(
        json.dumps(
            {
                "ready": True,
                "microphone": {
                    "path": "/private/student/microphone.wav",
                    "duration_seconds": 184.1,
                    "silence_ratio": 0.18,
                    "rms": 0.031,
                },
                "system": {
                    "path": "/private/student/system.wav",
                    "duration_seconds": 184.2,
                    "silence_ratio": 0.12,
                    "rms": 0.027,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EmptyTranscriptionError) as captured:
        validate_transcription_result(result, quality_report=quality)

    message = str(captured.value)
    assert "segment_count=0" in message
    assert "source_duration_seconds=184.2" in message
    assert "audio_quality_ready=True" in message
    assert "microphone_silence_ratio=0.18" in message
    assert "/private/student" not in message
    assert str(tmp_path) not in message


@pytest.mark.parametrize(
    ("duration", "text"),
    [
        (2.0, "Да."),
        (299.0, "Короткий корректный ответ."),
    ],
)
def test_validation_accepts_meaningful_transcript_regardless_of_short_duration(
    tmp_path,
    duration,
    text,
) -> None:
    result = _transcription_result(
        tmp_path,
        cleaned_text=text,
        segment_texts=[text],
        duration=duration,
    )

    validate_transcription_result(result)


def test_validation_rejects_punctuation_only_asr(tmp_path) -> None:
    result = _transcription_result(
        tmp_path,
        cleaned_text="...",
        segment_texts=["..."],
        duration=30.0,
    )

    with pytest.raises(EmptyTranscriptionError, match="meaningful_segment_count=0"):
        validate_transcription_result(result)
