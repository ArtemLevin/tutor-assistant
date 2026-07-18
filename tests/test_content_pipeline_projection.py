from __future__ import annotations

from datetime import date
from pathlib import Path

import tutor_assistant.content.service as content_service_module
from tutor_assistant.config import AppConfig
from tutor_assistant.content import AssetKind, LessonFilters, StudentContentService
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.pipeline import LessonPipeline
from tutor_assistant.transcription import TranscriptionResult


def make_lesson(lesson_id: str) -> Lesson:
    return Lesson(
        lesson_id=lesson_id,
        student=Student(id="student", full_name="Ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 18),
        topic="Автоматическая интеграция архива",
    )


class FakeTranscriber:
    def transcribe(self, _audio: Path, output_dir: Path) -> TranscriptionResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        raw = output_dir / "00_raw_whisper.txt"
        timestamped = output_dir / "00_raw_timestamped.txt"
        cleaned = output_dir / "03_content_only_medium.txt"
        segments = output_dir / "00_raw_segments.json"
        signals = output_dir / "important_student_signals.json"
        manifest = output_dir / "manifest.json"
        raw.write_text("Теорема Виета", encoding="utf-8")
        timestamped.write_text("[00:00] Теорема Виета", encoding="utf-8")
        cleaned.write_text("Теорема Виета и дискриминант", encoding="utf-8")
        segments.write_text("[]", encoding="utf-8")
        signals.write_text("[]", encoding="utf-8")
        manifest.write_text("{}", encoding="utf-8")
        return TranscriptionResult(
            output_dir=output_dir,
            raw=raw,
            timestamped=timestamped,
            cleaned=cleaned,
            segments=segments,
            signals=signals,
            manifest=manifest,
        )


def test_pipeline_projects_assets_revisions_and_search_without_manual_sync(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "data"
    config = AppConfig(workspace=workspace)
    config.recording.dual_channel_transcription = False
    pipeline = LessonPipeline(config)
    lesson = make_lesson("pipeline-archive")
    pipeline.create(lesson)
    recording = pipeline.lesson_dir(lesson) / "recording"
    recording.mkdir(parents=True)
    audio = recording / "mixed.wav"
    audio.write_bytes(b"RIFF-projected-audio")
    lesson.transition(JobStatus.RECORDED)
    pipeline.save_state(lesson, "status", "error")
    monkeypatch.setattr(pipeline, "transcriber", lambda: FakeTranscriber())
    monkeypatch.setattr(
        pipeline.content_service,
        "repair_archive",
        lambda: (_ for _ in ()).throw(AssertionError("manual repair must not be needed")),
    )

    lesson = pipeline.transcribe(lesson, audio)

    pending_review = pipeline.content_service.get_lesson(lesson.lesson_id)
    registered = {asset.relative_path: asset.kind for asset in pending_review.assets}
    assert pending_review.lesson.status == JobStatus.REVIEW_REQUIRED
    assert pending_review.transcript is None
    assert registered[f"lessons/{lesson.lesson_id}/recording/mixed.wav"] == AssetKind.AUDIO
    for name in (
        "00_raw_whisper.txt",
        "00_raw_timestamped.txt",
        "03_content_only_medium.txt",
        "transcript_verified.txt",
        "00_raw_segments.json",
        "important_student_signals.json",
        "manifest.json",
    ):
        assert f"lessons/{lesson.lesson_id}/transcript/{name}" in registered

    pipeline.approve_transcript(lesson, "Подтверждённая теорема Виета")

    archived = pipeline.content_service.get_lesson(lesson.lesson_id)
    search = pipeline.content_service.list_lessons(LessonFilters(query="подтверждённая Виета"))
    assert archived.lesson.status == JobStatus.READY
    assert archived.transcript is not None
    assert archived.transcript.revision_number == 1
    assert archived.transcript.created_by == "teacher-review"
    assert search.total == 1
    assert search.items[0].lesson_id == lesson.lesson_id


def test_archive_repair_preserves_sqlite_and_only_repairs_its_projection(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    service = StudentContentService(workspace)
    lesson = service.create_lesson(make_lesson("repair-source-of-truth"))
    directory = workspace / "lessons" / lesson.lesson_id
    stale = Lesson.read_json(directory / "lesson.json")
    stale.topic = "Устаревшая тема с диска"
    stale.write_json(directory / "lesson.json")
    generated = directory / "handbook.pdf"
    generated.write_bytes(b"%PDF-projected")

    report = service.repair_archive()

    content = service.get_lesson(lesson.lesson_id)
    disk = Lesson.read_json(directory / "lesson.json")
    assert report.errors == []
    assert content.lesson.topic == disk.topic == "Автоматическая интеграция архива"
    assert "Устаревшая тема с диска" not in content.lesson.model_dump_json()
    assert any(asset.relative_path.endswith("handbook.pdf") for asset in content.assets)
    assert service.list_lessons(LessonFilters(query="устаревшая тема")).total == 0


def test_incremental_projection_reactivates_a_recreated_asset(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    pipeline = LessonPipeline(AppConfig(workspace=workspace))
    lesson = make_lesson("reactivate-asset")
    pipeline.create(lesson)
    document = pipeline.lesson_dir(lesson) / "result.pdf"
    document.write_bytes(b"first")
    lesson.latex.pdf_path = str(document.resolve())
    pipeline.save_state(lesson, "latex")
    asset = next(
        item
        for item in pipeline.content_service.get_lesson(lesson.lesson_id).assets
        if item.relative_path.endswith("result.pdf")
    )
    assert asset.id is not None
    pipeline.content_service.delete_asset(asset.id)
    pipeline.save_state(lesson, "latex")
    assert all(item.id != asset.id for item in pipeline.content_service.get_lesson(lesson.lesson_id).assets)
    document.write_bytes(b"second")

    pipeline.save_state(lesson, "latex")

    restored = pipeline.content_service.get_lesson(lesson.lesson_id).assets
    assert any(item.id == asset.id and item.size_bytes == len(b"second") for item in restored)


def test_status_only_projection_does_not_read_an_active_recording(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pipeline = LessonPipeline(AppConfig(workspace=tmp_path / "data"))
    lesson = make_lesson("active-recording")
    pipeline.create(lesson)
    recording = pipeline.lesson_dir(lesson) / "recording" / "mixed.wav"
    recording.parent.mkdir(parents=True)
    recording.write_bytes(b"still-being-written")
    real_sha256 = content_service_module._sha256_file

    def reject_active_recording(path: Path) -> str:
        if path == recording:
            raise PermissionError("active recording is locked")
        return real_sha256(path)

    monkeypatch.setattr(content_service_module, "_sha256_file", reject_active_recording)
    lesson.transition(JobStatus.RECORDING)

    pipeline.save_state(lesson, "status", "error")

    assets = pipeline.content_service.get_lesson(lesson.lesson_id).assets
    assert all(not item.relative_path.endswith("mixed.wav") for item in assets)
