from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tutor_assistant.config import AppConfig, WhisperConfig
from tutor_assistant.transcription import GigaAMTranscriber, Segment, WhisperTranscriber


def test_faster_whisper_remains_default_provider() -> None:
    config = WhisperConfig()

    assert config.provider == "faster_whisper"
    assert config.model == "small"


def test_gigaam_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "app.yaml"
    config = AppConfig()
    config.whisper.provider = "gigaam"
    config.whisper.gigaam_model = "v3_e2e_rnnt"
    config.whisper.gigaam_device = "cuda"
    config.whisper.gigaam_chunk_seconds = 18.0

    config.save(path)
    restored = AppConfig.load(path)

    assert restored.whisper.provider == "gigaam"
    assert restored.whisper.gigaam_model == "v3_e2e_rnnt"
    assert restored.whisper.gigaam_device == "cuda"
    assert restored.whisper.gigaam_chunk_seconds == 18.0


def test_gigaam_chunk_must_stay_below_shortform_limit() -> None:
    with pytest.raises(ValidationError):
        WhisperConfig(gigaam_chunk_seconds=25.0)


def test_facade_delegates_to_gigaam(monkeypatch, tmp_path: Path) -> None:
    config = WhisperConfig(provider="gigaam")
    facade = WhisperTranscriber(config)
    expected = object()

    class FakeGigaAM:
        def transcribe(self, audio, output_dir):
            assert audio == tmp_path / "lesson.wav"
            assert output_dir == tmp_path / "out"
            return expected

    monkeypatch.setattr(facade, "_selected_gigaam", lambda: FakeGigaAM())

    assert facade.transcribe(tmp_path / "lesson.wav", tmp_path / "out") is expected


def test_gigaam_auto_device_is_delegated_to_library(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    model = object()

    def load_model(name, *, device=None):
        calls.append((name, device))
        return model

    monkeypatch.setitem(sys.modules, "gigaam", SimpleNamespace(load_model=load_model))
    transcriber = GigaAMTranscriber(
        WhisperConfig(provider="gigaam", gigaam_model="v3_e2e_rnnt", gigaam_device="auto")
    )

    assert transcriber._load() is model
    assert calls == [("v3_e2e_rnnt", None)]


def test_gigaam_recognition_preserves_offsets_and_word_timestamps(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = WhisperConfig(provider="gigaam", gigaam_chunk_seconds=20.0)
    transcriber = GigaAMTranscriber(config)
    chunk1 = tmp_path / "chunk_00000.wav"
    chunk2 = tmp_path / "chunk_00001.wav"
    chunk1.touch()
    chunk2.touch()

    results = iter(
        [
            SimpleNamespace(
                text="Первый фрагмент.",
                words=[
                    SimpleNamespace(text="Первый", start=0.2, end=0.8),
                    SimpleNamespace(text="фрагмент.", start=0.9, end=1.5),
                ],
            ),
            SimpleNamespace(
                text="Второй фрагмент.",
                words=[
                    SimpleNamespace(text="Второй", start=0.1, end=0.6),
                    SimpleNamespace(text="фрагмент.", start=0.7, end=1.2),
                ],
            ),
        ]
    )
    model = SimpleNamespace(transcribe=lambda _path, word_timestamps=True: next(results))
    monkeypatch.setattr(transcriber, "_load", lambda: model)
    monkeypatch.setattr(
        transcriber,
        "_segment_audio",
        lambda _audio, _directory: [chunk1, chunk2],
    )
    durations = iter([20.0, 7.5])
    monkeypatch.setattr(transcriber, "_chunk_duration", lambda _path: next(durations))

    segments, source = transcriber._recognize(
        tmp_path / "lesson.wav",
        speaker="У",
        offset_seconds=0.5,
    )

    assert [(item.start, item.end, item.speaker) for item in segments] == [
        (0.7, 2.0, "У"),
        (20.6, 21.7, "У"),
    ]
    assert source["provider"] == "gigaam"
    assert source["model"] == "v3_e2e_rnnt"
    assert source["duration_seconds"] == 27.5
    assert source["chunks"][1]["start_seconds"] == 20.0


def test_gigaam_manifest_records_provider_without_transcript_text(
    monkeypatch,
    tmp_path: Path,
) -> None:
    transcriber = GigaAMTranscriber(WhisperConfig(provider="gigaam"))

    def recognize(audio, *, speaker=None, offset_seconds=0.0):
        return [
            Segment(
                1.0,
                2.0,
                "Секретный учебный текст",
                None,
                None,
                speaker,
            )
        ], {
            "source_audio": str(audio),
            "provider": "gigaam",
            "model": "v3_e2e_rnnt",
            "chunks": [{"index": 0, "start_seconds": 0.0, "duration_seconds": 3.0}],
        }

    monkeypatch.setattr(transcriber, "_recognize", recognize)

    result = transcriber.transcribe(tmp_path / "lesson.wav", tmp_path / "out")
    manifest_text = result.manifest.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)

    assert manifest["provider"] == "gigaam"
    assert manifest["model"] == "v3_e2e_rnnt"
    assert "Секретный учебный текст" not in manifest_text
    assert "Секретный учебный текст" in result.raw.read_text(encoding="utf-8")
