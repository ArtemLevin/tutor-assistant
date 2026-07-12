from tutor_assistant.transcription import clean_transcript, extract_signals


def test_clean_transcript_removes_organizational_noise() -> None:
    source = "Здравствуйте. Меня слышно нормально? Решим уравнение x плюс два равно пяти."
    result = clean_transcript(source)
    assert "слышно" not in result.lower()
    assert "уравнение" in result.lower()


def test_extract_student_signals() -> None:
    result = extract_signals("Я не понимаю, почему здесь меняется знак.")
    assert result
    assert result[0]["signal"] == "не понимаю"

