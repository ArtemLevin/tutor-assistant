from __future__ import annotations

from tutor_assistant.normalization.models import NormalizationChunkRequest, SourceSegment
from tutor_assistant.security.cloud_consent import CloudRequestEnvelope


def test_cloud_envelope_excludes_local_identity_and_timestamps() -> None:
    request = NormalizationChunkRequest(
        lesson_id="student-private-lesson-id",
        prompt_version="filter-v3",
        mode="filter_only",
        lesson_subject="mathematics",
        subject_profile="mathematics",
        segments=[
            SourceSegment(
                source_segment_id=7,
                start=10.0,
                end=12.0,
                speaker="У",
                text="Решаю x + 2 = 5.",
            )
        ],
    )

    envelope = CloudRequestEnvelope.from_normalization_request(request)
    payload = envelope.model_dump(mode="json")
    serialized = envelope.model_dump_json()
    safe_request = envelope.as_normalization_request()

    assert "lesson_id" not in payload
    assert "start" not in payload["segments"][0]
    assert "end" not in payload["segments"][0]
    assert "student-private-lesson-id" not in serialized
    assert safe_request.lesson_id == "cloud-redacted"
    assert safe_request.segments[0].text == "Решаю x + 2 = 5."
