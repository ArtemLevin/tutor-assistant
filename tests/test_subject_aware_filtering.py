from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tutor_assistant.config import NormalizationConfig
from tutor_assistant.content import StudentContentService
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.normalization.errors import UnsafeNormalizationResultError
from tutor_assistant.normalization.models import SourceSegment
from tutor_assistant.normalization.prompts import system_prompt
from tutor_assistant.normalization.protocol import FakeNormalizationProvider
from tutor_assistant.normalization.service import NormalizationService
from tutor_assistant.normalization.subjects import (
    SubjectProfileName,
    resolve_subject_profile,
)
from tutor_assistant.normalization.validation import ValidationState, validate_plain_text_response


@pytest.mark.parametrize(
    ("subject", "expected"),
    (
        ("mathematics", SubjectProfileName.MATHEMATICS),
        ("ЕГЭ математика", SubjectProfileName.MATHEMATICS),
        ("Планиметрия", SubjectProfileName.MATHEMATICS),
        ("physics", SubjectProfileName.PHYSICS),
        ("Молекулярная физика", SubjectProfileName.PHYSICS),
        ("органическая химия", SubjectProfileName.CHEMISTRY),
        ("ЕГЭ химия", SubjectProfileName.CHEMISTRY),
        ("история", SubjectProfileName.GENERIC),
        ("", SubjectProfileName.GENERIC),
    ),
)
def test_subject_aliases_resolve_to_stable_profiles(subject: str, expected: SubjectProfileName) -> None:
    assert resolve_subject_profile(subject).name == expected


def test_subject_prompts_are_versioned_and_domain_specific() -> None:
    mathematics = resolve_subject_profile("mathematics")
    physics = resolve_subject_profile("physics")
    chemistry = resolve_subject_profile("chemistry")
    generic = resolve_subject_profile("history")

    assert mathematics.prompt_version == "educational-content-filter.mathematics.v2"
    assert physics.prompt_version == "educational-content-filter.physics.v1"
    assert chemistry.prompt_version == "educational-content-filter.chemistry.v1"
    assert generic.prompt_version == "educational-content-filter.generic.v1"
    assert "логариф" in system_prompt(mathematics.name).casefold()
    assert "импульс" in system_prompt(physics.name).casefold()
    assert "алкан" in system_prompt(chemistry.name).casefold()
    assert "основной учебный профиль" not in system_prompt(generic.name).casefold()


def test_regression_transcripts_preserve_subject_content() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "subject_filter_cases.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    for case in cases:
        segments = tuple(SourceSegment.model_validate(item) for item in case["segments"])
        profile = resolve_subject_profile(case["subject"])
        state = ValidationState()
        result = validate_plain_text_response(
            segments,
            tuple(segment.source_segment_id for segment in segments),
            case["filtered"],
            state,
            subject_profile=profile.name.value,
        )
        assert result == case["filtered"], case["name"]
        assert state.protected_content_preserved is True, case["name"]
        assert state.subject_units_preserved is True, case["name"]


@pytest.mark.parametrize(
    ("subject", "source", "unsafe"),
    (
        (
            "physics",
            "[П] Импульс тела равен 10 кг·м/с.",
            "[П] Тело равно 10.",
        ),
        (
            "chemistry",
            "[П] Количество вещества равно 2 моль.",
            "[П] Количество вещества равно 2.",
        ),
    ),
)
def test_subject_terms_and_units_cannot_be_removed(
    subject: str,
    source: str,
    unsafe: str,
) -> None:
    text = source.split("] ", 1)[1]
    segments = (SourceSegment(source_segment_id=1, speaker="П", text=text),)
    state = ValidationState()

    with pytest.raises(UnsafeNormalizationResultError):
        validate_plain_text_response(
            segments,
            (1,),
            unsafe,
            state,
            subject_profile=resolve_subject_profile(subject).name.value,
        )


def test_service_uses_lesson_subject_for_request_manifest_and_cache(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    content = StudentContentService(workspace)
    lesson = Lesson(
        lesson_id="physics-profile",
        student=Student(id="student", full_name="Обезличенный ученик"),
        subject="ЕГЭ физика",
        lesson_date=date(2026, 7, 27),
        topic="Импульс",
        status=JobStatus.REVIEW_REQUIRED,
    )
    lesson = content.create_lesson(lesson)
    transcript_dir = workspace / "lessons" / lesson.lesson_id / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    segments_path = transcript_dir / "00_raw_segments.json"
    segments_path.write_text(
        json.dumps(
            [
                {
                    "source_segment_id": 1,
                    "speaker": "П",
                    "text": "Импульс тела p = mv.",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    lesson.artifacts.segments_json = str(segments_path.resolve())
    content.persist_pipeline_lesson(lesson, frozenset({"artifacts"}))

    provider = FakeNormalizationProvider()
    service = NormalizationService(
        NormalizationConfig(retry_backoff_seconds=0),
        content,
        provider_factory=lambda _config, _model: provider,
    )

    execution = service.normalize_lesson(lesson.lesson_id)

    assert provider.requests[0].lesson_subject == "ЕГЭ физика"
    assert provider.requests[0].subject_profile == "physics"
    assert provider.requests[0].prompt_version == "educational-content-filter.physics.v1"
    assert execution.run is not None
    assert execution.run.prompt_version == "educational-content-filter.physics.v1"
    manifest = json.loads(Path(execution.manifest_path or "").read_text(encoding="utf-8"))
    assert manifest["lesson_subject"] == "ЕГЭ физика"
    assert manifest["subject_profile"] == "physics"
    assert execution.transcript.normalizer["subject_profile"] == "physics"
