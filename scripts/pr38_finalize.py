from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"marker mismatch for {path}: {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/tutor_assistant/normalization/service.py",
    '''                except NormalizationCancelledError:
                    if audit_event_id:
                        self.cloud_audit.finish_request(
                            audit_event_id,
                            event="request_indeterminate",
                            error_code="NormalizationCancelledError",
                        )
                    if run is not None:
                        self.checkpoints.reset_pending(run.id or 0, chunk.index)
                    raise
''',
    '''                except NormalizationCancelledError as exc:
                    if audit_event_id:
                        self.cloud_audit.finish_request(
                            audit_event_id,
                            event="request_indeterminate",
                            error_code="NormalizationCancelledError",
                        )
                    if run is not None:
                        if self.config.provider == "yandex_ai_studio":
                            self.checkpoints.mark_indeterminate(
                                run.id or 0,
                                chunk.index,
                                f"{type(exc).__name__}: {exc}",
                            )
                        else:
                            self.checkpoints.reset_pending(run.id or 0, chunk.index)
                    raise
''',
)

replace_once(
    "tests/test_cloud_service_consent.py",
    '''from tutor_assistant.normalization.errors import CloudProcessingConsentRequiredError
from tutor_assistant.normalization.protocol import FakeNormalizationProvider
''',
    '''from tutor_assistant.normalization.errors import (
    CloudProcessingConsentRequiredError,
    NormalizationCancelledError,
    NormalizationResumeConfirmationRequired,
)
from tutor_assistant.normalization.models import NormalizationChunkStatus
from tutor_assistant.normalization.protocol import FakeNormalizationProvider
''',
)

path = Path("tests/test_cloud_service_consent.py")
text = path.read_text(encoding="utf-8")
addition = '''


def test_cancelled_cloud_request_requires_explicit_retry_confirmation(tmp_path: Path) -> None:
    service, _content, lesson, provider = _setup(tmp_path)
    provider.responses.append(NormalizationCancelledError("cancelled after request dispatch"))
    request = service.cloud_processing_request(lesson.lesson_id)
    receipt = CloudConsentReceipt.grant(request)

    with pytest.raises(NormalizationCancelledError):
        service.normalize_lesson(lesson.lesson_id, cloud_consent=receipt)

    run = service.runs.latest(lesson.lesson_id)
    assert run is not None
    checkpoint = service.checkpoints.get(run.id or 0, 0)
    assert checkpoint is not None
    assert checkpoint.status == NormalizationChunkStatus.INDETERMINATE

    with pytest.raises(NormalizationResumeConfirmationRequired):
        service.normalize_lesson(lesson.lesson_id, cloud_consent=receipt)
'''
if "test_cancelled_cloud_request_requires_explicit_retry_confirmation" in text:
    raise RuntimeError("cancellation test already exists")
path.write_text(text.rstrip() + addition + "\n", encoding="utf-8")
