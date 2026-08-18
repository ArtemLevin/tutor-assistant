from tutor_assistant.application.transcription_queue import (
    TranscriptionQueueEntrySnapshot,
    TranscriptionQueueSnapshot,
)
from tutor_assistant.ui.transcription_queue_presentation import (
    build_transcription_queue_presentation,
)


def test_presentation_formats_rows_counts_and_badge() -> None:
    snapshot = TranscriptionQueueSnapshot(
        entries=(
            TranscriptionQueueEntrySnapshot(
                job_id="old",
                student_name="Иван",
                topic="Логарифмы",
                status="ready",
                error=None,
            ),
            TranscriptionQueueEntrySnapshot(
                job_id="new",
                student_name="Анна",
                topic="Планиметрия",
                status="failed",
                error="x" * 1700,
            ),
        ),
        unfinished_count=0,
        ready_count=1,
    )

    presentation = build_transcription_queue_presentation(snapshot)

    assert [row.job_id for row in presentation.rows] == ["new", "old"]
    assert presentation.rows[0].text == "Ошибка  ·  Анна  ·  Планиметрия"
    assert presentation.rows[1].text == "Готов к проверке  ·  Иван  ·  Логарифмы"
    assert len(presentation.rows[0].tooltip or "") == 1500
    assert presentation.summary_text == "В обработке: 0 · готовы к проверке: 1"
    assert presentation.badge_text == "≡ 1"
    assert presentation.badge_tooltip == (
        "В обработке: 0\n"
        "Готовы к проверке: 1\n"
        "Нажмите, чтобы открыть очередь"
    )


def test_presentation_handles_empty_queue() -> None:
    presentation = build_transcription_queue_presentation(
        TranscriptionQueueSnapshot(entries=(), unfinished_count=0, ready_count=0)
    )

    assert presentation.rows == ()
    assert presentation.summary_text == "В обработке: 0 · готовы к проверке: 0"
    assert presentation.badge_text == "≡ 0"
