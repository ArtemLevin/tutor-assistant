import json

from tutor_assistant.config import WhisperConfig
from tutor_assistant.transcription import Segment, WhisperTranscriber, clean_transcript, extract_signals


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
        text = "Объяснение" if speaker == "Преподаватель" else "Я не понимаю"
        start = 1.0 if speaker == "Преподаватель" else 2.0
        return [Segment(start + offset_seconds, start + offset_seconds + 0.5, text, -0.1, 0.0, speaker)], {
            "speaker": speaker,
            "source_audio": str(audio),
        }

    monkeypatch.setattr(transcriber, "_recognize", recognize)
    result = transcriber.transcribe_dual(tmp_path / "mic.wav", tmp_path / "system.wav", tmp_path / "out")
    segments = json.loads(result.segments.read_text(encoding="utf-8"))
    signals = json.loads(result.signals.read_text(encoding="utf-8"))
    assert [item["speaker"] for item in segments] == ["Преподаватель", "Ученик"]
    assert signals[0]["speaker"] == "Ученик"
    assert result.teacher_transcript.exists()
    assert result.student_transcript.exists()
